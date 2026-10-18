"""Tick data loading and SYNTHETIC tick generation.

Real data: export bid/ask ticks from the broker with ``mql5/Scripts/LRB_ExportTicks.mq5``
(CSV ``time_msc_utc,bid,ask``). The MT5 "Symbols > Ticks > Export" format (tab separated
``<DATE> <TIME> <BID> <ASK> ...`` in *server* time) is also accepted with a UTC offset.

Synthetic data is ONLY for exercising the engine (stress paths, plumbing, regression
tests). It is not market data and results on it are not evidence of profitability.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from lrb.core.types import Tick


def load_ticks_csv(path: str | Path, server_utc_offset_hours: float = 0.0) -> list[Tick]:
    path = Path(path)
    ticks: list[Tick] = []
    with path.open(newline="") as f:
        first = f.readline()
        f.seek(0)
        if first.startswith("<DATE>"):
            reader = csv.reader(f, delimiter="\t")
            header = [h.strip("<>").upper() for h in next(reader)]
            ib, ia = header.index("BID"), header.index("ASK")
            last_bid = last_ask = math.nan
            for row in reader:
                dt = datetime.strptime(f"{row[0]} {row[1]}", "%Y.%m.%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                bid = float(row[ib]) if row[ib] else last_bid
                ask = float(row[ia]) if row[ia] else last_ask
                last_bid, last_ask = bid, ask
                if math.isfinite(bid) and math.isfinite(ask):
                    ticks.append(Tick(dt.timestamp() - server_utc_offset_hours * 3600, bid, ask))
        else:
            reader = csv.DictReader(f)
            for row in reader:
                ticks.append(Tick(int(row["time_msc_utc"]) / 1000.0, float(row["bid"]), float(row["ask"])))
    ticks.sort(key=lambda t: t.time)
    bad = [t for t in ticks if t.ask < t.bid]
    if bad:
        raise ValueError(f"{len(bad)} ticks with ask < bid in {path}")
    return ticks


@dataclass
class SynthParams:
    start: float = 1_735_689_600.0  # 2025-01-01 00:00 UTC (Wed); synthetic calendar only
    days: int = 60
    price0: float = 1.1000
    tick_interval_sec: float = 6.0
    base_vol_per_sqrt_sec: float = 0.0000045  # ~1.6e-4 per hour-ish noise
    point: float = 0.00001
    spread_points: float = 8.0
    trend_prob_per_hour: float = 0.04
    trend_drift_per_hour: float = 0.0012
    trend_hours: tuple[float, float] = (3.0, 18.0)
    vol_regime_prob_per_hour: float = 0.03
    jump_prob_per_hour: float = 0.004
    jump_size: float = 0.0030
    rollover_spread_mult: float = 6.0
    weekend_gap_sd: float = 0.0015
    seed: int = 1


def synthetic_ticks(p: SynthParams) -> Iterator[Tick]:
    """Regime-switching random walk with trends, vol regimes, jumps, spread spikes and weekend gaps."""
    rng = random.Random(p.seed)
    t = p.start
    end = p.start + p.days * 86400
    mid = p.price0
    drift = 0.0
    trend_left = 0.0
    vol_mult = 1.0
    while t < end:
        dt_ = rng.expovariate(1.0 / p.tick_interval_sec)
        t_next = t + dt_
        wd = datetime.fromtimestamp(t_next, tz=timezone.utc).weekday()
        if wd >= 5:  # weekend: no ticks, then a gap at Monday open
            monday = (math.floor(t_next / 86400) + (7 - wd)) * 86400
            t = monday + 60
            mid += rng.gauss(0, p.weekend_gap_sd)
            continue
        hours = dt_ / 3600.0
        if trend_left <= 0 and rng.random() < p.trend_prob_per_hour * hours:
            trend_left = rng.uniform(*p.trend_hours)
            drift = rng.choice((-1, 1)) * p.trend_drift_per_hour * rng.uniform(0.5, 1.5)
        if trend_left > 0:
            trend_left -= hours
            if trend_left <= 0:
                drift = 0.0
        if rng.random() < p.vol_regime_prob_per_hour * hours:
            vol_mult = rng.choice((0.6, 1.0, 1.0, 1.6, 2.5))
        mid += drift * hours + rng.gauss(0, p.base_vol_per_sqrt_sec * vol_mult * math.sqrt(dt_))
        if rng.random() < p.jump_prob_per_hour * hours:
            mid += rng.choice((-1, 1)) * p.jump_size * rng.uniform(0.5, 1.5)
        hour = (t_next % 86400) / 3600.0
        spread = p.spread_points * rng.uniform(0.8, 1.6)
        if hour >= 23.9 or hour < 0.15:  # rollover spread spike
            spread *= p.rollover_spread_mult
        if rng.random() < 0.0005:
            spread *= rng.uniform(3, 10)
        bid = round(mid - spread * p.point / 2, 5)
        ask = round(bid + max(1, round(spread)) * p.point, 5)
        t = t_next
        yield Tick(t, bid, ask)


def write_ticks_csv(ticks: Iterator[Tick] | list[Tick], path: str | Path) -> int:
    n = 0
    with Path(path).open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_msc_utc", "bid", "ask"])
        for tk in ticks:
            w.writerow([int(round(tk.time * 1000)), f"{tk.bid:.5f}", f"{tk.ask:.5f}"])
            n += 1
    return n
