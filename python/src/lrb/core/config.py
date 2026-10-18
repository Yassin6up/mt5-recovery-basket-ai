"""Strategy and risk configuration.

All defaults are conservative *research* parameters for demo trading. They are not
validated recommendations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import Enum
from typing import Any


class BoundaryMode(str, Enum):
    FIXED = "FIXED"  # boundaries are frozen when leg 1 fills (only mode implemented)


class AIMode(str, Enum):
    OFF = "OFF"  # strategy A: deterministic baseline only
    FILTER = "FILTER"  # strategy B: a valid, unexpired AI ALLOW is required for new baskets


class LegApprovalMode(str, Enum):
    OFF = "OFF"  # recovery legs are deterministic
    REQUIRED = "REQUIRED"  # a valid cached AI ALLOW is required per recovery leg


@dataclass
class StrategyConfig:
    symbol: str = "EURUSD"
    magic: int = 26_101_801

    # --- progression -------------------------------------------------------------
    progression: tuple[float, ...] = (1.0, 2.0, 3.0)  # multipliers of the base lot
    max_positions: int = 3

    # --- boundaries (frozen at basket start) ------------------------------------
    boundary_mode: BoundaryMode = BoundaryMode.FIXED
    atr_timeframe_sec: int = 900  # M15
    atr_period: int = 14
    atr_mult: float = 1.0  # D = max(atr_mult*ATR, min_distance, spread_mult*spread)
    min_distance_points: float = 150.0
    max_distance_points: float = 1500.0
    min_distance_spread_mult: float = 4.0
    stop_distance_mult: float = 1.0  # Dstop = stop_distance_mult * D beyond the boundary

    # --- baseline research signal (NOT a proven edge) ---------------------------
    signal_timeframe_sec: int = 900
    ema_fast: int = 20
    ema_slow: int = 50
    min_trend_atr_frac: float = 0.10  # |EMAf-EMAs| >= frac * ATR

    # --- basket risk / exits ------------------------------------------------------
    risk_pct: float = 1.0  # max basket risk, % of equity at basket start
    target_pct: float = 0.5  # basket profit target, % of equity at basket start
    sizing_safety: float = 0.10  # fraction of the risk budget held back for gaps/slippage
    max_basket_duration_sec: int = 48 * 3600
    cooldown_sec: int = 15 * 60

    # --- account guards -------------------------------------------------------------
    daily_loss_pct: float = 2.0  # of start-of-day equity, floating losses included
    max_drawdown_pct: float = 6.0  # from equity high-water mark; halts until manual reset
    day_rollover_utc_hour: int = 0

    # --- exposure / margin --------------------------------------------------------
    max_gross_lots: float = 1.0
    max_net_lots: float = 0.5
    min_margin_level_pct: float = 500.0  # projected margin level required before any leg
    emergency_margin_level_pct: float = 200.0  # close the basket below this
    min_free_margin_after_pct: float = 50.0  # free margin after leg >= % of equity

    # --- execution filters ----------------------------------------------------------
    max_spread_points: float = 25.0  # entry
    max_leg_spread_points: float = 40.0  # recovery legs (leg deferred while wider)
    max_tick_age_sec: float = 10.0
    max_trigger_slippage_points: float = 50.0  # beyond the boundary => skip leg, stop adding
    deviation_points: int = 10
    leg_max_attempts: int = 2
    pending_ack_timeout_sec: float = 10.0
    close_retry_sec: float = 1.0
    max_exec_errors_per_hour: int = 5
    news_filter_enabled: bool = False  # if enabled and calendar unavailable => block entries

    # --- cost model (estimates; broker deals provide the truth) ---------------------
    commission_per_lot_side: float = 3.5  # account currency, charged per side
    slippage_allowance_points: float = 5.0  # per fill, used in sizing and close estimate
    spread_stress_mult: float = 2.0  # sizing assumes spread at exit = mult * current

    # --- AI -----------------------------------------------------------------------------
    ai_mode: AIMode = AIMode.OFF
    leg_approval: LegApprovalMode = LegApprovalMode.OFF
    ai_max_data_age_sec: float = 1200.0

    # --- safety -------------------------------------------------------------------------
    demo_only: bool = True
    allow_live: bool = False  # must ALSO be explicitly enabled in the EA; off by default

    def validate(self) -> None:
        if not self.progression or any(m <= 0 for m in self.progression):
            raise ValueError("progression must be non-empty positive multipliers")
        if self.max_positions < 1:
            raise ValueError("max_positions must be >= 1")
        if self.max_positions > len(self.progression):
            raise ValueError("max_positions exceeds progression length")
        if not (0 < self.risk_pct <= 5):
            raise ValueError("risk_pct must be in (0, 5]")
        if self.target_pct <= 0:
            raise ValueError("target_pct must be > 0")
        if not (0 <= self.sizing_safety < 1):
            raise ValueError("sizing_safety must be in [0, 1)")
        if self.min_distance_points <= 0 or self.max_distance_points < self.min_distance_points:
            raise ValueError("invalid distance bounds")
        if self.stop_distance_mult <= 0:
            raise ValueError("stop_distance_mult must be > 0")
        if self.boundary_mode is not BoundaryMode.FIXED:
            raise ValueError("only FIXED boundary mode is implemented")
        if self.allow_live and self.demo_only:
            raise ValueError("allow_live requires demo_only=False")

    @property
    def legs(self) -> tuple[float, ...]:
        return tuple(self.progression[: self.max_positions])

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Enum):
                d[k] = v.value
            elif isinstance(v, tuple):
                d[k] = list(v)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StrategyConfig":
        kwargs: dict[str, Any] = {}
        types = {f.name: f for f in fields(cls)}
        for k, v in d.items():
            if k not in types:
                raise ValueError(f"unknown config key: {k}")
            default = getattr(cls(), k)
            if isinstance(default, Enum):
                v = type(default)(v)
            elif isinstance(default, tuple):
                v = tuple(float(x) for x in v)
            kwargs[k] = v
        cfg = cls(**kwargs)
        cfg.validate()
        return cfg
