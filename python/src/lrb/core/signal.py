"""Research baseline entry signal.

RESEARCH BASELINE -- NOT A PROVEN EDGE. It exists so that entries are reproducible and so
the AI filter has something well-defined to filter.

Rule, evaluated once per closed bar of ``signal_timeframe_sec`` (bid bars):
    f = EMA(close, ema_fast), s = EMA(close, ema_slow), a = ATR(atr_period)
    BUY  if f - s >=  min_trend_atr_frac * a
    SELL if s - f >=  min_trend_atr_frac * a
    otherwise no signal.
A signal is only valid until the next bar closes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .config import StrategyConfig
from .indicators import atr, ema
from .types import Bar, Side


@dataclass(frozen=True)
class SignalSnapshot:
    bar_time: float  # open time of the last closed bar used
    close_time: float  # close time of that bar (= decision data timestamp)
    direction: Side | None
    atr: float
    ema_fast: float
    ema_slow: float
    reason: str


def baseline_signal(closed_bars: Sequence[Bar], cfg: StrategyConfig, period_sec: int | None = None) -> SignalSnapshot | None:
    period_sec = period_sec or cfg.signal_timeframe_sec
    need = max(cfg.ema_slow, cfg.atr_period + 1) + 5
    if len(closed_bars) < need:
        return None
    window = closed_bars[-max(need * 4, 300):]
    closes = [b.close for b in window]
    f = float(ema(closes, cfg.ema_fast)[-1])
    s = float(ema(closes, cfg.ema_slow)[-1])
    a = atr(window, cfg.atr_period)
    last = closed_bars[-1]
    if not (math.isfinite(f) and math.isfinite(s) and math.isfinite(a)) or a <= 0:
        return SignalSnapshot(last.time, last.time + period_sec, None, a, f, s, "INDICATORS_UNAVAILABLE")
    if f - s >= cfg.min_trend_atr_frac * a:
        d, r = Side.BUY, "EMA_TREND_UP"
    elif s - f >= cfg.min_trend_atr_frac * a:
        d, r = Side.SELL, "EMA_TREND_DOWN"
    else:
        d, r = None, "NO_TREND"
    return SignalSnapshot(last.time, last.time + period_sec, d, a, f, s, r)
