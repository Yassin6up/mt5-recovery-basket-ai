from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(int, Enum):
    BUY = 1
    SELL = -1

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY


@dataclass(frozen=True)
class Tick:
    time: float  # epoch seconds, UTC
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


@dataclass
class Position:
    ticket: int
    symbol: str
    magic: int
    side: Side
    volume: float
    open_price: float
    open_time: float
    comment: str = ""
    swap: float = 0.0  # accumulated swap of the open position (broker POSITION_SWAP)


class DealEntry(str, Enum):
    IN = "IN"
    OUT = "OUT"


@dataclass(frozen=True)
class Deal:
    ticket: int
    position_id: int
    symbol: str
    magic: int
    entry: DealEntry
    side: Side  # side of the deal itself (a closing deal of a BUY position is a SELL)
    volume: float
    price: float
    time: float
    profit: float = 0.0  # realized price P&L (OUT deals only)
    swap: float = 0.0  # swap realized at close (OUT deals only)
    commission: float = 0.0  # negative = charged
    fee: float = 0.0  # negative = charged
    comment: str = ""


class Retcode(str, Enum):
    DONE = "DONE"
    DONE_PARTIAL = "DONE_PARTIAL"
    REJECTED = "REJECTED"
    REQUOTE = "REQUOTE"
    NO_MONEY = "NO_MONEY"
    MARKET_CLOSED = "MARKET_CLOSED"
    TIMEOUT = "TIMEOUT"  # outcome unknown: must be reconciled against positions/deals


@dataclass(frozen=True)
class OrderResult:
    retcode: Retcode
    filled_volume: float = 0.0
    price: float = 0.0
    position_ticket: int = 0
    message: str = ""


@dataclass
class AccountState:
    balance: float
    equity: float
    margin: float
    currency: str = "USD"
    is_demo: bool = True
    is_hedging: bool = True
    leverage: float = 100.0

    @property
    def free_margin(self) -> float:
        return self.equity - self.margin

    @property
    def margin_level(self) -> float:
        """Margin level in percent; +inf when no margin is used."""
        return float("inf") if self.margin <= 0 else self.equity / self.margin * 100.0


@dataclass(frozen=True)
class SymbolSpec:
    """Broker symbol specification. Values must come from the broker (SymbolInfo*)."""

    name: str = "EURUSD"
    digits: int = 5
    point: float = 0.00001
    contract_size: float = 100_000.0
    tick_size: float = 0.00001
    tick_value_loss: float = 1.0  # account currency per tick per 1.0 lot, for losses
    tick_value_profit: float = 1.0
    volume_min: float = 0.01
    volume_max: float = 100.0
    volume_step: float = 0.01
    margin_initial_per_lot: float = 0.0  # 0 => contract*price/leverage
    hedged_margin_ratio: float = 1.0  # 1.0 = no hedge reduction assumed (conservative)

    @property
    def value_per_price_unit_loss(self) -> float:
        """Account-currency loss for a 1.0 price move on 1.0 lot (conservative tick value)."""
        return self.tick_value_loss / self.tick_size

    @property
    def value_per_price_unit_profit(self) -> float:
        return self.tick_value_profit / self.tick_size


@dataclass
class Bar:
    time: float  # bar open time, epoch seconds UTC
    open: float
    high: float
    low: float
    close: float
    spread_max: float = 0.0
    tick_count: int = 0


@dataclass(frozen=True)
class AIDecision:
    request_id: str
    decision: str  # ALLOW | BLOCK | WAIT
    direction: str  # BUY | SELL | NONE
    model_version: str
    data_timestamp_utc: float
    expires_at_utc: float
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    kind: str = "ENTRY"  # ENTRY | RECOVERY_LEG
    leg_index: int = 0
