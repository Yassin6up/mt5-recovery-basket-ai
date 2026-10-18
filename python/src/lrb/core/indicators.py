"""Indicators shared by the backtester, the AI feature pipeline and (ported) the EA.

Bars are built from BID prices. All functions only use data up to and including the
last *closed* bar passed in, so they cannot look ahead.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from .types import Bar, Tick


def ema(values: Sequence[float], period: int) -> np.ndarray:
    """EMA seeded with the SMA of the first ``period`` values (same as MT5 iMA EMA after warm-up)."""
    x = np.asarray(values, dtype=float)
    out = np.full(x.shape, np.nan)
    if len(x) < period or period <= 0:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = x[:period].mean()
    for i in range(period, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def true_range(bars: Sequence[Bar]) -> np.ndarray:
    tr = np.empty(len(bars))
    for i, b in enumerate(bars):
        if i == 0:
            tr[i] = b.high - b.low
        else:
            pc = bars[i - 1].close
            tr[i] = max(b.high - b.low, abs(b.high - pc), abs(b.low - pc))
    return tr


def atr(bars: Sequence[Bar], period: int) -> float:
    """Wilder ATR of the given (closed) bars; NaN if not enough data."""
    if len(bars) < period + 1:
        return math.nan
    tr = true_range(bars)[1:]
    a = tr[:period].mean()
    for v in tr[period:]:
        a = (a * (period - 1) + v) / period
    return float(a)


class BarBuilder:
    """Aggregates ticks into fixed-period bid bars aligned to UTC epoch multiples."""

    def __init__(self, period_sec: int, max_bars: int = 2000):
        self.period = period_sec
        self.max_bars = max_bars
        self.closed: list[Bar] = []
        self.current: Bar | None = None

    def update(self, tick: Tick) -> bool:
        """Feed one tick. Returns True if a bar was closed by this tick."""
        start = math.floor(tick.time / self.period) * self.period
        closed = False
        if self.current is None or start > self.current.time:
            if self.current is not None:
                self.closed.append(self.current)
                if len(self.closed) > self.max_bars:
                    del self.closed[: len(self.closed) - self.max_bars]
                closed = True
            self.current = Bar(start, tick.bid, tick.bid, tick.bid, tick.bid, tick.spread, 1)
        else:
            c = self.current
            c.high = max(c.high, tick.bid)
            c.low = min(c.low, tick.bid)
            c.close = tick.bid
            c.spread_max = max(c.spread_max, tick.spread)
            c.tick_count += 1
        return closed
