"""Basket accounting.

Each money component is taken from exactly one source so nothing is double-counted:

* ``floating``  - broker floating profit of open basket positions. MT5 marks longs at the
  bid and shorts at the ask, so the closing spread is ALREADY inside this number; no
  separate spread cost is added.
* ``open_swap`` - accumulated swap of *open* positions (POSITION_SWAP).
* ``realized``  - profit + swap of OUT deals of basket positions (partial or full
  closes). Once a position is closed its swap moves from ``open_swap`` to here.
* ``commissions`` - commission + fee of ALL basket deals already executed (IN and OUT).
* ``est_close_costs`` - estimate for what has NOT been charged yet: exit commission
  per remaining lot plus a slippage allowance on the remaining volume.

net = floating + open_swap + realized + commissions - est_close_costs
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from .types import Deal, DealEntry, Position, SymbolSpec


@dataclass(frozen=True)
class BasketPnL:
    floating: float
    open_swap: float
    realized: float
    commissions: float
    est_close_costs: float
    open_volume: float

    @property
    def net(self) -> float:
        return self.floating + self.open_swap + self.realized + self.commissions - self.est_close_costs

    @property
    def net_if_closed_now_ex_costs(self) -> float:
        return self.floating + self.open_swap + self.realized + self.commissions


def basket_pnl(
    positions: Iterable[Position],
    position_profit: Callable[[Position], float],
    deals: Iterable[Deal],
    position_ids: set[int],
    spec: SymbolSpec,
    exit_commission_per_lot: float,
    slippage_allowance_points: float,
) -> BasketPnL:
    floating = open_swap = open_vol = 0.0
    for p in positions:
        floating += position_profit(p)
        open_swap += p.swap
        open_vol += p.volume
    realized = commissions = 0.0
    seen: set[int] = set()
    for d in deals:
        if d.position_id not in position_ids or d.ticket in seen:
            continue
        seen.add(d.ticket)
        commissions += d.commission + d.fee
        if d.entry is DealEntry.OUT:
            realized += d.profit + d.swap
    est = open_vol * exit_commission_per_lot + open_vol * slippage_allowance_points * spec.point * spec.value_per_price_unit_loss
    return BasketPnL(floating, open_swap, realized, commissions, est, open_vol)


def final_realized(deals: Iterable[Deal], position_ids: set[int]) -> float:
    """Final realized basket P&L from broker deal history (the authoritative number)."""
    total = 0.0
    seen: set[int] = set()
    for d in deals:
        if d.position_id in position_ids and d.ticket not in seen:
            seen.add(d.ticket)
            total += d.profit + d.swap + d.commission + d.fee
    return total
