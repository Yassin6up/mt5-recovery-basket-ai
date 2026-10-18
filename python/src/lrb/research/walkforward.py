"""Chronological walk-forward evaluation: strategy A (no AI) vs B (AI filter).

Layout over the data span [T0, T1):
    pre-holdout [T0, H) is cut into n_folds + 2 equal chunks c0..c_{n+1}
    fold j: train = c0..cj (expanding), validation = c_{j+1} (Platt calibration only),
            test = c_{j+2}
    final holdout [H, T1): touched ONCE with a model trained on c0..c_n / validated on c_{n+1}.
Nothing in the holdout is used for fitting, calibration, or parameter choice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from lrb.ai.decide import ModelProvider
from lrb.ai.model import LogisticModel, brier, log_loss, reliability
from lrb.core.config import AIMode, StrategyConfig
from lrb.core.types import SymbolSpec, Tick

from .backtest import BrokerModel, run_backtest
from .labels import LabelSet, build_labels
from .metrics import Metrics


@dataclass
class SegmentResult:
    name: str
    train: tuple[float, float]
    validation: tuple[float, float]
    test: tuple[float, float]
    n_train: int
    n_val: int
    n_test_labels: int
    model_eval: dict[str, Any]
    a: Metrics
    b: Metrics
    ai_calls: int
    model_path: str | None = None


@dataclass
class WalkForwardReport:
    folds: list[SegmentResult] = field(default_factory=list)
    holdout: SegmentResult | None = None
    data_note: str = ""


def _slice(ticks: list[Tick], start: float, end: float) -> list[Tick]:
    import bisect
    times = [t.time for t in ticks]
    return ticks[bisect.bisect_left(times, start):bisect.bisect_left(times, end)]


def _evaluate_model(model: LogisticModel | None, test: LabelSet) -> dict[str, Any]:
    if model is None or len(test) == 0:
        return {"n": len(test), "note": "no model or no test labels"}
    p = model.p_loss_batch(test.X)
    base = np.full_like(test.y, test.y.mean())
    return {
        "n": int(len(test)), "base_rate_loss": float(test.y.mean()),
        "brier": brier(p, test.y), "brier_base_rate": brier(base, test.y),
        "log_loss": log_loss(p, test.y), "reliability": reliability(p, test.y),
    }


def _segment(name, ticks, labels, cfg, spec, bm, tr, va, te, warmup_sec, min_train, model_dir) -> SegmentResult:
    L_tr, L_va, L_te = labels.select(*tr), labels.select(*va), labels.select(*te)
    model = None
    if len(L_tr) >= min_train and 0 < L_tr.y.mean() < 1:
        model = LogisticModel.train(L_tr.X, L_tr.y, L_tr.pnl, L_va.X, L_va.y, version=f"logit-{name}",
                                    meta={"train": tr, "validation": va, "n_train": len(L_tr), "n_val": len(L_va)})
    seg_ticks = _slice(ticks, te[0] - warmup_sec, te[1])
    cfg_a = replace(cfg, ai_mode=AIMode.OFF)
    res_a = run_backtest(seg_ticks, cfg_a, spec, bm, trade_from=te[0])
    calls = 0
    if model is not None:
        cfg_b = replace(cfg, ai_mode=AIMode.FILTER)
        holder = {}

        def factory(bars):
            holder["p"] = ModelProvider(bars, model, cfg.signal_timeframe_sec, max_data_age_sec=cfg.ai_max_data_age_sec)
            return holder["p"]

        res_b = run_backtest(seg_ticks, cfg_b, spec, bm, ai_factory=factory, trade_from=te[0])
        calls = holder["p"].calls
        mb = res_b.metrics
    else:
        mb = res_a.metrics
        mb = replace(mb, notes="NO MODEL TRAINED (insufficient labels): B == A")
    path = None
    if model is not None and model_dir:
        path = f"{model_dir}/{model.version}.json"
        model.save(path)
    return SegmentResult(name, tr, va, te, len(L_tr), len(L_va), len(L_te), _evaluate_model(model, L_te),
                         res_a.metrics, mb, calls, path)


def walk_forward(ticks: list[Tick], cfg: StrategyConfig, spec: SymbolSpec | None = None,
                 bm: BrokerModel | None = None, n_folds: int = 3, holdout_frac: float = 0.2,
                 label_spacing_sec: float = 7200.0, warmup_sec: float = 5 * 86400, min_train: int = 30,
                 model_dir: str | None = None, labels: LabelSet | None = None) -> WalkForwardReport:
    spec = spec or SymbolSpec(name=cfg.symbol)
    bm = bm or BrokerModel(commission_per_lot_side=cfg.commission_per_lot_side)
    T0, T1 = ticks[0].time, ticks[-1].time
    H = T0 + (T1 - T0) * (1 - holdout_frac)
    chunk = (H - T0) / (n_folds + 2)
    c = [T0 + i * chunk for i in range(n_folds + 3)]
    labels = labels if labels is not None else build_labels(ticks, cfg, spec, bm, label_spacing_sec)
    rep = WalkForwardReport()
    for j in range(n_folds):
        rep.folds.append(_segment(f"fold{j+1}", ticks, labels, cfg, spec, bm, (c[0], c[j + 1]), (c[j + 1], c[j + 2]),
                                  (c[j + 2], c[j + 3]), warmup_sec, min_train, model_dir))
    rep.holdout = _segment("holdout", ticks, labels, cfg, spec, bm, (c[0], c[n_folds + 1]), (c[n_folds + 1], H),
                           (H, T1 + 1), warmup_sec, min_train, model_dir)
    return rep
