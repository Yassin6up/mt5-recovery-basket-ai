"""Research CLI.

    lrb-backtest synth --days 120 --out data/synth_ticks.csv
    lrb-backtest run --ticks data/ticks.csv [--config cfg.json] [--model models/m.json]
    lrb-backtest walkforward --ticks data/ticks.csv --out reports/walkforward.json
    lrb-backtest stress
    lrb-backtest train --ticks data/ticks.csv --out models/entry_model.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

from lrb.ai.decide import ModelProvider
from lrb.ai.model import LogisticModel
from lrb.core.config import AIMode, StrategyConfig
from lrb.core.types import SymbolSpec

from .backtest import BrokerModel, run_backtest
from .data import SynthParams, load_ticks_csv, synthetic_ticks, write_ticks_csv
from .labels import build_labels
from .metrics import format_metrics
from .stress import default_scenarios, format_stress
from .walkforward import walk_forward


def _cfg(path: str | None) -> StrategyConfig:
    if not path:
        return StrategyConfig()
    d = json.loads(Path(path).read_text())
    return StrategyConfig.from_dict(d.get("strategy", d))


def _spec(path: str | None, cfg: StrategyConfig) -> SymbolSpec:
    if not path:
        return SymbolSpec(name=cfg.symbol)
    return SymbolSpec(**json.loads(Path(path).read_text()))


def _ticks(args):
    ticks = load_ticks_csv(args.ticks, args.server_utc_offset)
    synthetic = "synth" in Path(args.ticks).name.lower()
    banner = "SYNTHETIC DATA - engine exercise only, NOT evidence of profitability" if synthetic else \
        f"Tick data: {args.ticks} ({len(ticks)} ticks)"
    print(banner, file=sys.stderr)
    return ticks, synthetic


def _json_default(o):
    if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
        return str(o)
    if hasattr(o, "__dataclass_fields__"):
        return asdict(o)
    return str(o)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lrb-backtest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synth")
    s.add_argument("--days", type=int, default=120)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--out", required=True)
    for name in ("run", "walkforward", "train"):
        p = sub.add_parser(name)
        p.add_argument("--ticks", required=True)
        p.add_argument("--config")
        p.add_argument("--symbol-spec", help="JSON SymbolSpec exported from the broker")
        p.add_argument("--server-utc-offset", type=float, default=0.0)
        if name == "run":
            p.add_argument("--model", help="LogisticModel JSON; enables AI filter (strategy B)")
        if name in ("walkforward", "train"):
            p.add_argument("--out", required=True)
            p.add_argument("--folds", type=int, default=3)
            p.add_argument("--holdout", type=float, default=0.2)
            p.add_argument("--label-spacing", type=float, default=7200.0)
    sub.add_parser("stress")
    args = ap.parse_args(argv)

    if args.cmd == "synth":
        n = write_ticks_csv(synthetic_ticks(SynthParams(days=args.days, seed=args.seed)), args.out)
        print(f"wrote {n} SYNTHETIC ticks to {args.out}")
        return 0
    if args.cmd == "stress":
        print(format_stress(default_scenarios()))
        return 0
    cfg = _cfg(args.config)
    spec = _spec(args.symbol_spec, cfg)
    ticks, synthetic = _ticks(args)
    bm = BrokerModel(commission_per_lot_side=cfg.commission_per_lot_side)
    if args.cmd == "run":
        res_a = run_backtest(ticks, cfg, spec, bm)
        print(format_metrics(res_a.metrics, "A: strategy without AI"))
        if args.model:
            model = LogisticModel.load(args.model)
            cfg_b = StrategyConfig.from_dict({**cfg.to_dict(), "ai_mode": AIMode.FILTER.value})
            res_b = run_backtest(ticks, cfg_b, spec, bm, ai_factory=lambda bars: ModelProvider(
                bars, model, cfg.signal_timeframe_sec, max_data_age_sec=cfg.ai_max_data_age_sec))
            print(format_metrics(res_b.metrics, f"B: strategy with AI filter ({model.version})"))
            print("WARNING: if the model was trained on any part of these ticks this comparison is in-sample.")
        return 0
    if args.cmd == "walkforward":
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        model_dir = str(Path(args.out).parent / "wf_models")
        Path(model_dir).mkdir(exist_ok=True)
        rep = walk_forward(ticks, cfg, spec, bm, n_folds=args.folds, holdout_frac=args.holdout,
                           label_spacing_sec=args.label_spacing, model_dir=model_dir)
        rep.data_note = "SYNTHETIC" if synthetic else args.ticks
        for seg in rep.folds + [rep.holdout]:
            print(f"\n######## {seg.name}: labels train={seg.n_train} val={seg.n_val} test={seg.n_test_labels}")
            print(json.dumps(seg.model_eval, default=_json_default))
            print(format_metrics(seg.a, f"{seg.name} A (no AI)"))
            print(format_metrics(seg.b, f"{seg.name} B (AI filter, {seg.ai_calls} calls)"))
        Path(args.out).write_text(json.dumps(asdict(rep), default=_json_default, indent=2))
        print(f"\nreport written to {args.out}")
        return 0
    if args.cmd == "train":
        # Deployment model: train on all data except the most recent validation slice (calibration).
        labels = build_labels(ticks, cfg, spec, bm, args.label_spacing)
        T0, T1 = ticks[0].time, ticks[-1].time
        v = T0 + (T1 - T0) * (1 - args.holdout)
        tr, va = labels.select(T0, v), labels.select(v, T1 + 1)
        if len(tr) < 30:
            print(f"only {len(tr)} training labels - refusing to train", file=sys.stderr)
            return 2
        m = LogisticModel.train(tr.X, tr.y, tr.pnl, va.X, va.y, version=f"logit-{int(T1)}",
                                meta={"train": [T0, v], "validation": [v, T1], "n_train": len(tr), "n_val": len(va),
                                      "data": "SYNTHETIC" if synthetic else str(args.ticks),
                                      "warning": "validate out-of-sample with `walkforward` before use"})
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        m.save(args.out)
        print(f"model saved to {args.out} (n_train={len(tr)}, n_val={len(va)})")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
