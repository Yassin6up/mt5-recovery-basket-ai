"""Abstract broker view used by the strategy engine.

In production the MQL5 EA implements the same operations with the MT5 trade API; the
Python engine is used for research/backtesting and tests only and never connects to a
real broker.
"""

from __future__ import annotations

from typing import Protocol

from .types import AccountState, Deal, OrderResult, Position, Side, SymbolSpec, Tick


class Broker(Protocol):
    def account(self) -> AccountState: ...

    def spec(self) -> SymbolSpec: ...

    def last_tick(self) -> Tick: ...

    def positions(self, symbol: str, magic: int) -> list[Position]: ...

    def position_profit(self, position: Position) -> float:
        """Floating price P&L at the closing side (longs at bid, shorts at ask), excl. swap/commission."""
        ...

    def deals(self, since: float) -> list[Deal]: ...

    def send_market(self, side: Side, volume: float, magic: int, comment: str, deviation_points: int) -> OrderResult: ...

    def close_position(self, ticket: int, deviation_points: int) -> OrderResult: ...

    def calc_margin(self, side: Side, volume: float, price: float) -> float: ...

    def market_open(self, t: float) -> bool: ...
