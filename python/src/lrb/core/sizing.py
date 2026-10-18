"""Boundary definition and base-lot sizing.

Coordinate conventions (documented in docs/STRATEGY_SPEC.md):

* BUY legs execute at the ask and trigger on the ask crossing UP through ``upper``.
* SELL legs execute at the bid and trigger on the bid crossing DOWN through ``lower``.
* ``upper - lower == D`` (boundary distance), fixed for the life of the basket.
* Initial BUY:  upper = leg-1 fill price (ask), lower = upper - D.
* Initial SELL: lower = leg-1 fill price (bid), upper = lower + D.
* Hard price stops: ``stop_low = lower - Dstop`` (checked on bid),
  ``stop_high = upper + Dstop`` (checked on ask), ``Dstop = stop_distance_mult * D``.
  Only the side that is adverse to the basket's current net exposure is active
  (net long -> stop_low, net short -> stop_high, net flat -> both).

Sizing: the base lot ``q`` is the largest volume (floored to the broker step) such that,
for every reachable leg state k = 1..n (including states where later legs are blocked),
the loss when price reaches the active stop boundary -- with stressed spread, adverse
slippage on every fill and round-turn commission -- does not exceed
``risk_budget * (1 - sizing_safety)``. If the broker minimum volume exceeds that, the
basket is rejected (volume is never rounded up).

LIMITATION: this is a *planned* loss. Gaps, slippage beyond the allowance, requotes,
outages and broker stop-outs can make the realized loss larger. A software stop cannot
guarantee the maximum loss.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import StrategyConfig
from .types import Side, SymbolSpec


def floor_volume(volume: float, spec: SymbolSpec) -> float:
    """Floor ``volume`` to the broker step. Returns 0.0 if below the broker minimum."""
    if volume <= 0 or not math.isfinite(volume):
        return 0.0
    volume = min(volume, spec.volume_max)
    steps = math.floor(volume / spec.volume_step + 1e-9)
    v = round(steps * spec.volume_step, 8)
    return v if v + 1e-12 >= spec.volume_min else 0.0


def leg_sides(direction: Side, n: int) -> list[Side]:
    return [direction if i % 2 == 0 else direction.opposite for i in range(n)]


@dataclass(frozen=True)
class Levels:
    upper: float
    lower: float
    stop_low: float
    stop_high: float
    distance: float
    stop_distance: float


def levels_from_anchor(direction: Side, anchor: float, distance: float, stop_mult: float) -> Levels:
    if direction is Side.BUY:
        upper, lower = anchor, anchor - distance
    else:
        lower, upper = anchor, anchor + distance
    dstop = stop_mult * distance
    return Levels(upper, lower, lower - dstop, upper + dstop, distance, dstop)


def boundary_distance(atr: float, spread: float, cfg: StrategyConfig, spec: SymbolSpec) -> tuple[float, str | None]:
    """Return (D, rejection_reason). D is in price units."""
    if not math.isfinite(atr) or atr <= 0:
        return 0.0, "ATR_UNAVAILABLE"
    d = max(cfg.atr_mult * atr, cfg.min_distance_points * spec.point, cfg.min_distance_spread_mult * spread)
    if d > cfg.max_distance_points * spec.point:
        return d, "DISTANCE_TOO_WIDE"
    return d, None


@dataclass
class Scenario:
    legs_filled: int
    stop_side: str  # "LOW" | "HIGH"
    net_multiplier: float
    loss_price_units: float  # per 1.0 base lot (multipliers applied)
    loss_money_per_lot: float  # incl. commission


@dataclass
class SizingResult:
    ok: bool
    reason: str | None
    base_lot: float = 0.0
    volumes: list[float] = field(default_factory=list)
    risk_budget: float = 0.0
    usable_budget: float = 0.0
    distance: float = 0.0
    stop_distance: float = 0.0
    worst_loss_per_lot: float = 0.0
    planned_max_loss: float = 0.0
    raw_lot: float = 0.0
    caps: dict[str, float] = field(default_factory=dict)
    scenarios: list[Scenario] = field(default_factory=list)

    def explain(self) -> str:
        lines = [
            f"ok={self.ok} reason={self.reason}",
            f"risk_budget={self.risk_budget:.2f} usable(after safety)={self.usable_budget:.2f}",
            f"D={self.distance:.6f} Dstop={self.stop_distance:.6f}",
            f"worst loss per 1.0 base lot={self.worst_loss_per_lot:.2f}",
            f"raw lot={self.raw_lot:.6f} caps={self.caps} -> base lot={self.base_lot}",
            f"leg volumes={self.volumes} planned max loss={self.planned_max_loss:.2f}",
        ]
        for s in self.scenarios:
            lines.append(
                f"  state k={s.legs_filled} stop={s.stop_side} net={s.net_multiplier:+g} "
                f"loss/lot={s.loss_money_per_lot:.2f}"
            )
        return "\n".join(lines)


def worst_case_scenarios(
    direction: Side, distance: float, spread: float, cfg: StrategyConfig, spec: SymbolSpec
) -> list[Scenario]:
    mults = cfg.legs
    sides = leg_sides(direction, len(mults))
    lv = levels_from_anchor(direction, 0.0 if direction is Side.SELL else distance, distance, cfg.stop_distance_mult)
    slip = cfg.slippage_allowance_points * spec.point
    spread_x = max(spread, spec.point) * cfg.spread_stress_mult
    v = spec.value_per_price_unit_loss

    entries: list[float] = []
    for i, s in enumerate(sides):
        leg_slip = 0.0 if i == 0 else slip  # leg 1 fill *is* the anchor
        entries.append(lv.upper + leg_slip if s is Side.BUY else lv.lower - leg_slip)

    out: list[Scenario] = []
    for k in range(1, len(mults) + 1):
        net = sum(mults[i] * sides[i].value for i in range(k))
        stop_sides = ["LOW"] if net > 0 else ["HIGH"] if net < 0 else ["LOW", "HIGH"]
        for ss in stop_sides:
            if ss == "LOW":
                bid = lv.stop_low
                ask = bid + spread_x
            else:
                ask = lv.stop_high
                bid = ask - spread_x
            pnl = 0.0
            for i in range(k):
                if sides[i] is Side.BUY:
                    pnl += mults[i] * ((bid - slip) - entries[i])
                else:
                    pnl += mults[i] * (entries[i] - (ask + slip))
            loss_price = -pnl
            commission = 2.0 * cfg.commission_per_lot_side * sum(mults[:k])
            out.append(Scenario(k, ss, net, loss_price, loss_price * v + commission))
    return out


def compute_base_lot(
    direction: Side,
    equity_at_start: float,
    atr: float,
    spread: float,
    cfg: StrategyConfig,
    spec: SymbolSpec,
    risk_budget_override: float | None = None,
) -> SizingResult:
    distance, why = boundary_distance(atr, spread, cfg, spec)
    if why:
        return SizingResult(False, why, distance=distance)
    budget = cfg.risk_pct / 100.0 * equity_at_start
    if risk_budget_override is not None:
        budget = min(budget, risk_budget_override)
    if budget <= 0:
        return SizingResult(False, "NO_RISK_BUDGET", risk_budget=budget, distance=distance)
    usable = budget * (1.0 - cfg.sizing_safety)
    scen = worst_case_scenarios(direction, distance, spread, cfg, spec)
    worst = max(s.loss_money_per_lot for s in scen)
    res = SizingResult(
        False, None, risk_budget=budget, usable_budget=usable, distance=distance,
        stop_distance=distance * cfg.stop_distance_mult, worst_loss_per_lot=worst, scenarios=scen,
    )
    if worst <= 0:
        res.reason = "SIZING_DEGENERATE"
        return res
    raw = usable / worst
    mults = cfg.legs
    sides = leg_sides(direction, len(mults))
    max_net = max(abs(sum(mults[i] * sides[i].value for i in range(k))) for k in range(1, len(mults) + 1))
    caps = {"risk": raw, "gross": cfg.max_gross_lots / sum(mults)}
    if max_net > 0:
        caps["net"] = cfg.max_net_lots / max_net
    res.raw_lot = raw
    res.caps = caps
    q = floor_volume(min(caps.values()), spec)
    if q <= 0:
        res.reason = "MIN_VOLUME_EXCEEDS_RISK_BUDGET"
        return res
    vols = [floor_volume(q * m, spec) for m in mults]
    if any(v <= 0 for v in vols):
        res.reason = "LEG_VOLUME_BELOW_MIN"
        return res
    # Re-check with the floored leg volumes (flooring can only reduce risk, but be exact).
    # Conservative (and identical to Sizing.mqh): worst price loss + round-turn commission of ALL legs.
    planned = max(s.loss_price_units for s in scen) * spec.value_per_price_unit_loss * q \
        + 2.0 * cfg.commission_per_lot_side * sum(vols)
    res.ok, res.base_lot, res.volumes, res.planned_max_loss = True, q, vols, planned
    if planned > budget + 1e-9:
        res.ok, res.reason = False, "PLANNED_LOSS_EXCEEDS_BUDGET"
    return res
