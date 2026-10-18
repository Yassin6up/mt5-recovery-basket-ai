"""Tick-level simulated hedging broker with cost model and fault injection.

Simplifications (documented in docs/VALIDATION.md):
* Fills happen at the current tick's bid/ask plus a random ADVERSE slippage in points.
* Margin per lot = margin_initial_per_lot, or contract*price/leverage (quote currency ==
  account currency assumed). No hedge margin reduction unless hedged_margin_ratio < 1.
* Swap is charged at the daily rollover (00:00 UTC), triple on Wednesday.
* Stop-out closes the most losing position while margin level <= stop_out_level_pct.
* Tick value is constant (no currency conversion drift).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from typing import Callable

from lrb.core.types import (
    AccountState, Deal, DealEntry, OrderResult, Position, Retcode, Side, SymbolSpec, Tick,
)


@dataclass
class Faults:
    reject_sends: int = 0  # reject the next N market orders
    timeout_sends: int = 0  # fill the next N orders but report TIMEOUT (unknown outcome)
    partial_ratio: float = 1.0  # fill this fraction of the next order (once)
    close_failures: int = 0  # fail the next N close requests
    market_closed: bool = False


@dataclass
class SimBroker:
    spec_: SymbolSpec
    balance: float = 10_000.0
    leverage: float = 100.0
    commission_per_lot_side: float = 3.5
    swap_long_points: float = -7.0
    swap_short_points: float = 2.0
    max_slippage_points: float = 2.0
    stop_out_level_pct: float = 50.0
    is_demo: bool = True
    is_hedging: bool = True
    seed: int = 7
    session: Callable[[float], bool] | None = None
    faults: Faults = field(default_factory=Faults)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)
        self._tick: Tick | None = None
        self._positions: dict[int, Position] = {}
        self._magic_of: dict[int, int] = {}
        self._deals: list[Deal] = []
        self._next_ticket = 1000
        self._last_rollover_day: int | None = None
        self.stop_outs = 0

    # --------------------------------------------------------------- market data
    def set_tick(self, tick: Tick) -> None:
        if self._tick is not None:
            prev_day = math.floor(self._tick.time / 86400)
            day = math.floor(tick.time / 86400)
            if day > prev_day:
                for d in range(prev_day + 1, day + 1):
                    self._apply_swap(d)
        self._tick = tick
        self._check_stop_out()

    def last_tick(self) -> Tick:
        assert self._tick is not None
        return self._tick

    def spec(self) -> SymbolSpec:
        return self.spec_

    def market_open(self, t: float) -> bool:
        if self.faults.market_closed:
            return False
        return self.session(t) if self.session else True

    # --------------------------------------------------------------- account
    def position_profit(self, p: Position) -> float:
        t = self.last_tick()
        move = (t.bid - p.open_price) if p.side is Side.BUY else (p.open_price - t.ask)
        v = self.spec_.value_per_price_unit_profit if move >= 0 else self.spec_.value_per_price_unit_loss
        return move * v * p.volume

    def _margin_per_lot(self, price: float) -> float:
        s = self.spec_
        return s.margin_initial_per_lot or s.contract_size * price / self.leverage

    def calc_margin(self, side: Side, volume: float, price: float) -> float:
        return self._margin_per_lot(price) * volume

    def used_margin(self) -> float:
        t = self.last_tick()
        longs = sum(p.volume for p in self._positions.values() if p.side is Side.BUY)
        shorts = sum(p.volume for p in self._positions.values() if p.side is Side.SELL)
        hedged = min(longs, shorts)
        r = self.spec_.hedged_margin_ratio
        lots = abs(longs - shorts) + 2 * hedged * r
        return lots * self._margin_per_lot(t.mid)

    def account(self) -> AccountState:
        floating = sum(self.position_profit(p) + p.swap for p in self._positions.values())
        return AccountState(self.balance, self.balance + floating, self.used_margin(),
                            is_demo=self.is_demo, is_hedging=self.is_hedging, leverage=self.leverage)

    def positions(self, symbol: str, magic: int) -> list[Position]:
        return [replace(p) for p in self._positions.values() if p.magic == magic and p.symbol == symbol]

    def all_positions(self) -> list[Position]:
        return [replace(p) for p in self._positions.values()]

    def deals(self, since: float) -> list[Deal]:
        return [d for d in self._deals if d.time >= since]

    # --------------------------------------------------------------- trading
    def _slip(self) -> float:
        return self._rng.uniform(0, self.max_slippage_points) * self.spec_.point

    def _commission(self, volume: float) -> float:
        return -self.commission_per_lot_side * volume

    def send_market(self, side: Side, volume: float, magic: int, comment: str, deviation_points: int) -> OrderResult:
        t = self.last_tick()
        if not self.market_open(t.time):
            return OrderResult(Retcode.MARKET_CLOSED)
        if self.faults.reject_sends > 0:
            self.faults.reject_sends -= 1
            return OrderResult(Retcode.REJECTED, message="injected reject")
        if self.calc_margin(side, volume, t.ask) > self.account().free_margin:
            return OrderResult(Retcode.NO_MONEY)
        filled = volume
        if self.faults.partial_ratio < 1.0:
            step = self.spec_.volume_step
            filled = max(self.spec_.volume_min, math.floor(volume * self.faults.partial_ratio / step) * step)
            filled = round(filled, 8)
            self.faults.partial_ratio = 1.0
        slip = self._slip()
        if slip > deviation_points * self.spec_.point:
            slip = deviation_points * self.spec_.point
        price = t.ask + slip if side is Side.BUY else t.bid - slip
        ticket = self._next_ticket
        self._next_ticket += 1
        self._positions[ticket] = Position(ticket, self.spec_.name, magic, side, filled, price, t.time, comment)
        self.balance += self._commission(filled)  # MT5 debits entry-deal commission immediately
        self._deals.append(Deal(self._next_deal(), ticket, self.spec_.name, magic, DealEntry.IN, side, filled,
                                price, t.time, commission=self._commission(filled), comment=comment))
        if self.faults.timeout_sends > 0:
            self.faults.timeout_sends -= 1
            return OrderResult(Retcode.TIMEOUT, message="injected timeout (order WAS filled)")
        code = Retcode.DONE if filled >= volume - 1e-9 else Retcode.DONE_PARTIAL
        return OrderResult(code, filled, price, ticket)

    def _next_deal(self) -> int:
        self._next_ticket += 1
        return self._next_ticket

    def close_position(self, ticket: int, deviation_points: int) -> OrderResult:
        t = self.last_tick()
        if ticket not in self._positions:
            return OrderResult(Retcode.REJECTED, message="no such position")
        if not self.market_open(t.time):
            return OrderResult(Retcode.MARKET_CLOSED)
        if self.faults.close_failures > 0:
            self.faults.close_failures -= 1
            return OrderResult(Retcode.REJECTED, message="injected close failure")
        return self._close(ticket, "close")

    def _close(self, ticket: int, comment: str) -> OrderResult:
        t = self.last_tick()
        p = self._positions.pop(ticket)
        slip = self._slip()
        price = t.bid - slip if p.side is Side.BUY else t.ask + slip
        move = (price - p.open_price) if p.side is Side.BUY else (p.open_price - price)
        v = self.spec_.value_per_price_unit_profit if move >= 0 else self.spec_.value_per_price_unit_loss
        profit = move * v * p.volume
        comm = self._commission(p.volume)
        self.balance += profit + p.swap + comm
        self._deals.append(Deal(self._next_deal(), ticket, p.symbol, p.magic, DealEntry.OUT, p.side.opposite,
                                p.volume, price, t.time, profit=profit, swap=p.swap, commission=comm, comment=comment))
        return OrderResult(Retcode.DONE, p.volume, price, ticket)

    def _apply_swap(self, day: int) -> None:
        weekday = (day + 3) % 7  # 1970-01-01 was a Thursday -> Monday=0
        mult = 3 if weekday == 2 else 1  # Wednesday rollover charges 3 days
        pt = self.spec_.point * self.spec_.value_per_price_unit_loss
        for p in self._positions.values():
            pts = self.swap_long_points if p.side is Side.BUY else self.swap_short_points
            p.swap += pts * pt * p.volume * mult
            # balance unaffected until close (MT5 keeps swap on the position)

    def _check_stop_out(self) -> None:
        while self._positions:
            acct = self.account()
            if acct.margin <= 0 or acct.margin_level > self.stop_out_level_pct:
                return
            worst = min(self._positions.values(), key=lambda p: self.position_profit(p) + p.swap)
            self._close(worst.ticket, "so")
            self.stop_outs += 1

    # --------------------------------------------------------------- manual / other-EA helpers for tests
    def open_foreign(self, side: Side, volume: float, magic: int, comment: str = "manual") -> int:
        res = self.send_market(side, volume, magic, comment, 1000)
        return res.position_ticket

    def force_close(self, ticket: int) -> None:
        self._close(ticket, "external")
