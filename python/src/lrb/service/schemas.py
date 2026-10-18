"""Strict request/response schemas (unknown fields rejected)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BarIn(Strict):
    t: int = Field(ge=946684800, description="bar OPEN time, epoch seconds UTC")
    o: float = Field(gt=0)
    h: float = Field(gt=0)
    l: float = Field(gt=0)
    c: float = Field(gt=0)

    @model_validator(mode="after")
    def _ohlc(self):
        if not (self.l <= min(self.o, self.c) and self.h >= max(self.o, self.c)):
            raise ValueError("inconsistent OHLC")
        return self


class AccountIn(Strict):
    equity: float
    balance: float
    margin: float = Field(ge=0)
    drawdown_pct: float = Field(ge=0, le=100)
    daily_pnl_pct: float = 0.0
    gross_lots: float = Field(ge=0, default=0.0)
    net_lots: float = 0.0


class DecisionRequest(Strict):
    request_id: str = Field(pattern=r"^[A-Za-z0-9:_\-]{8,64}$")
    kind: Literal["ENTRY", "RECOVERY_LEG"]
    symbol: str = Field(min_length=1, max_length=32)
    magic: int = Field(ge=0)
    proposed_direction: Literal["BUY", "SELL"]
    leg_index: int = Field(ge=0, le=10, default=0)
    sent_at_utc: float
    timeframe_sec: Literal[60, 300, 900, 1800, 3600, 14400]
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    distance: float = Field(ge=0)
    account: AccountIn
    bars: list[BarIn] = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def _consistency(self):
        if self.ask < self.bid:
            raise ValueError("ask < bid")
        ts = [b.t for b in self.bars]
        if any(b <= a for a, b in zip(ts, ts[1:])):
            raise ValueError("bars must be strictly ascending")
        if self.kind == "RECOVERY_LEG" and self.leg_index < 2:
            raise ValueError("recovery legs start at index 2")
        return self


class Probability(Strict):
    p_basket_loss: float
    outcome: str
    horizon: str
    calibration: str
    model_evaluation: dict | None = None


class DecisionResponse(Strict):
    request_id: str
    kind: Literal["ENTRY", "RECOVERY_LEG"]
    leg_index: int
    decision: Literal["ALLOW", "BLOCK", "WAIT"]
    direction: Literal["BUY", "SELL", "NONE"]
    model_version: str
    data_timestamp_utc: str
    expires_at_utc: str
    data_timestamp_utc_epoch: float
    expires_at_utc_epoch: float
    reason_codes: list[str]
    probability: Probability | None = None
    disclaimer: str = "Research filter. No profit guarantee. The EA risk engine has final authority."


class LegIn(Strict):
    index: int
    side: Literal["BUY", "SELL"]
    planned_volume: float
    state: str
    ticket: int = 0
    filled_volume: float = 0.0
    fill_price: float = 0.0


class BasketIn(Strict):
    basket_id: str = ""
    phase: str
    direction: str = ""
    upper: float = 0.0
    lower: float = 0.0
    stop_low: float = 0.0
    stop_high: float = 0.0
    net_pnl: float = 0.0
    floating: float = 0.0
    realized: float = 0.0
    commissions: float = 0.0
    swap: float = 0.0
    target_money: float = 0.0
    risk_money: float = 0.0
    adding_allowed: bool = False
    block_reason: str = ""
    close_reason: str = ""
    legs: list[LegIn] = Field(default_factory=list, max_length=10)


class TelemetryIn(Strict):
    terminal_id: str = Field(pattern=r"^[A-Za-z0-9_\-]{1,40}$")
    ea_version: str = Field(max_length=20)
    time_utc: float
    symbol: str
    magic: int
    balance: float
    equity: float
    margin: float
    margin_level: float = 0.0
    currency: str = Field(max_length=8)
    is_demo: bool
    trading_allowed: bool
    daily_halt: bool
    dd_halt: bool
    kill: str
    sod_equity: float
    peak_equity: float
    daily_loss_limit_pct: float
    max_drawdown_pct: float
    risk_pct: float
    target_pct: float
    ai_mode: str
    ai_status: str
    last_ai_decision: str = ""
    entries_blocked_reason: str = ""
    errors: list[str] = Field(default_factory=list, max_length=20)
    basket: BasketIn

    @field_validator("errors")
    @classmethod
    def _trim(cls, v: list[str]) -> list[str]:
        return [e[:300] for e in v]
