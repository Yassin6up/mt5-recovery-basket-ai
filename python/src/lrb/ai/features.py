"""Feature extraction from CLOSED bars only (identical code in research and the service).

Every feature is computable at decision time from data timestamped at or before the
close of the last closed bar. Direction-dependent features are signed so that positive
values mean "aligned with the proposed direction".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from lrb.core.indicators import atr, ema
from lrb.core.types import Bar

FEATURE_NAMES: tuple[str, ...] = (
    "ret_1_atr", "ret_4_atr", "ret_16_atr", "ema_gap_atr", "ema_slope_atr", "atr_rel", "rv_ratio",
    "range_pos", "spread_atr", "hour_sin", "hour_cos", "dist_atr", "drawdown_pct",
)
MIN_BARS = 120


@dataclass(frozen=True)
class FeatureRow:
    names: tuple[str, ...]
    values: np.ndarray
    data_timestamp_utc: float  # close time of the last bar used


def compute_features(
    bars: Sequence[Bar], direction: int, period_sec: int, spread: float, distance: float,
    drawdown_pct: float = 0.0, ema_fast: int = 20, ema_slow: int = 50, atr_period: int = 14,
) -> FeatureRow | None:
    if len(bars) < MIN_BARS or direction not in (1, -1):
        return None
    w = bars[-MIN_BARS:]
    c = np.array([b.close for b in w])
    a = atr(w, atr_period)
    if not math.isfinite(a) or a <= 0:
        return None
    a100 = atr(w[-101:], 100) if len(w) >= 101 else a
    ef, es = ema(c, ema_fast), ema(c, ema_slow)
    lr = np.diff(np.log(c))
    rv20 = float(np.std(lr[-20:]))
    rv100 = float(np.std(lr[-100:])) or 1e-12
    hi, lo = max(b.high for b in w[-48:]), min(b.low for b in w[-48:])
    rng = hi - lo
    close_t = w[-1].time + period_sec
    hour = (close_t % 86400) / 3600.0
    d = float(direction)
    vals = np.array([
        d * (c[-1] - c[-2]) / a,
        d * (c[-1] - c[-5]) / a,
        d * (c[-1] - c[-17]) / a,
        d * (ef[-1] - es[-1]) / a,
        d * (ef[-1] - ef[-5]) / a,
        a / a100 if a100 > 0 else 1.0,
        rv20 / rv100,
        d * (((c[-1] - lo) / rng) * 2 - 1) if rng > 0 else 0.0,
        spread / a,
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
        distance / a,
        drawdown_pct,
    ])
    if not np.all(np.isfinite(vals)):
        return None
    return FeatureRow(FEATURE_NAMES, vals, close_t)
