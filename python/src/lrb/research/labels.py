"""Supervised labels for the entry filter.

Outcome: for a baseline signal at the close of bar t with direction d, open a standalone
basket at the next tick and run the full deterministic engine until the basket closes
(bounded by max_basket_duration_sec). Label y = 1 if realized P&L <= 0 else 0.

Leakage controls:
* features use only bars closed at decision time;
* each sample records t_start and t_end (label horizon); splits PURGE samples whose
  horizon crosses the split boundary (see ``select``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from lrb.ai.features import FEATURE_NAMES, compute_features
from lrb.core.config import AIMode, StrategyConfig
from lrb.core.engine import Engine
from lrb.core.indicators import BarBuilder
from lrb.core.signal import baseline_signal
from lrb.core.sizing import boundary_distance
from lrb.core.types import SymbolSpec, Tick

from .backtest import BrokerModel


@dataclass
class LabelSet:
    X: np.ndarray
    y: np.ndarray
    pnl: np.ndarray
    t_start: np.ndarray
    t_end: np.ndarray

    def select(self, start: float, end: float) -> "LabelSet":
        """Samples that start in [start, end) AND whose outcome is known before ``end`` (purged)."""
        m = (self.t_start >= start) & (self.t_start < end) & (self.t_end < end)
        return LabelSet(self.X[m], self.y[m], self.pnl[m], self.t_start[m], self.t_end[m])

    def __len__(self) -> int:
        return len(self.y)


def build_labels(ticks: list[Tick], cfg: StrategyConfig, spec: SymbolSpec, bm: BrokerModel,
                 spacing_sec: float = 7200.0) -> LabelSet:
    cfg_l = replace(cfg, ai_mode=AIMode.OFF, cooldown_sec=0)
    bars = BarBuilder(cfg.signal_timeframe_sec)
    rows, ys, pnls, t0s, t1s = [], [], [], [], []
    last_cand = -1e18
    horizon = cfg.max_basket_duration_sec + 3600
    n = len(ticks)
    for i, tk in enumerate(ticks):
        if not bars.update(tk):
            continue
        sig = baseline_signal(bars.closed, cfg)
        if sig is None or sig.direction is None or tk.time - last_cand < spacing_sec:
            continue
        dist, why = boundary_distance(sig.atr, tk.spread, cfg, spec)
        if why:
            continue
        f = compute_features(bars.closed, sig.direction.value, cfg.signal_timeframe_sec, tk.spread, dist, 0.0,
                             cfg.ema_fast, cfg.ema_slow, cfg.atr_period)
        if f is None:
            continue
        broker = bm.make(spec)
        eng = Engine(cfg_l, broker)
        j = i
        while j < n and ticks[j].time - tk.time <= horizon:
            broker.set_tick(ticks[j])
            eng.on_tick(ticks[j], sig if j == i else None)
            if j == i and eng.basket is None:
                break
            if eng.reports:
                break
            j += 1
        if not eng.reports:
            continue  # rejected by filters, or outcome not observed within the data
        last_cand = tk.time
        rep = eng.reports[0]
        rows.append(f.values)
        ys.append(1.0 if rep.realized_pnl <= 0 else 0.0)
        pnls.append(rep.realized_pnl)
        t0s.append(tk.time)
        t1s.append(rep.end_time)
    X = np.array(rows) if rows else np.zeros((0, len(FEATURE_NAMES)))
    return LabelSet(X, np.array(ys), np.array(pnls), np.array(t0s), np.array(t1s))
