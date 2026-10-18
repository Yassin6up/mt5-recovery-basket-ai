"""Deterministic stress scenarios run through the full engine + simulated broker.

Each scenario is a scripted bid path (plus optional faults / outages / restarts). These
are engineering stress tests, not market evidence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from lrb.core.config import AIMode, StrategyConfig
from lrb.core.engine import Engine
from lrb.core.signal import SignalSnapshot
from lrb.core.types import AIDecision, Side, SymbolSpec, Tick

from .simbroker import Faults, SimBroker

T0 = 1_760_000_000.0
SPREAD = 0.00010


@dataclass
class StressResult:
    name: str
    baskets: int
    realized: float
    worst_basket: float
    max_positions_seen: int
    close_reasons: list[str]
    final_open_positions: int
    planned_risk: float
    exceeded_planned_risk: bool
    notes: str = ""


def _path_sideways(start: float, d: float, cycles: int) -> list[float]:
    out = []
    for _ in range(cycles):
        out += [start - d * 1.15 * math.sin(math.pi * i / 40) for i in range(80)]
    return out


def _ramp(a: float, b: float, n: int) -> list[float]:
    return [a + (b - a) * i / n for i in range(1, n + 1)]


class _ToggleAI:
    def __init__(self):
        self.down = False

    def _d(self, ctx, kind):
        if self.down:
            return None
        t = ctx["time"]
        return AIDecision("s", "ALLOW", ctx["proposed_direction"], "stress", t, t + 60, (), kind, ctx.get("leg_index", 0))

    def entry_decision(self, ctx):
        return self._d(ctx, "ENTRY")

    def leg_decision(self, ctx):
        return self._d(ctx, "RECOVERY_LEG")


def run_scenario(name: str, bids: list[float], cfg: StrategyConfig, signal_at: dict[int, Side],
                 spreads: dict[int, float] | None = None, faults_at: dict[int, Callable[[SimBroker], None]] | None = None,
                 restart_at: set[int] | None = None, ai: _ToggleAI | None = None,
                 ai_down: tuple[int, int] | None = None, leverage: float = 100.0, gaps: dict[int, float] | None = None
                 ) -> StressResult:
    broker = SimBroker(SymbolSpec(), leverage=leverage, max_slippage_points=1.0, faults=Faults())
    eng = Engine(cfg, broker, ai=ai)
    t = T0
    max_pos = 0
    planned = 0.0
    for i, bid in enumerate(bids):
        t += (gaps or {}).get(i, 1.0)
        if faults_at and i in faults_at:
            faults_at[i](broker)
        if ai and ai_down:
            ai.down = ai_down[0] <= i < ai_down[1]
        if restart_at and i in restart_at:
            saved = eng.to_json()
            reports = eng.reports
            eng = Engine(cfg, broker, ai=ai)
            eng.load_json(saved)
            eng.reports = reports
        sp = (spreads or {}).get(i, SPREAD)
        tk = Tick(t, round(bid, 5), round(bid + sp, 5))
        broker.set_tick(tk)
        sig = SignalSnapshot(i, t, signal_at[i], 0.0020, 0, 0, "STRESS") if i in signal_at else None
        eng.on_tick(tk, sig)
        if eng.basket is not None:
            planned = max(planned, eng.basket.risk_money)
        max_pos = max(max_pos, len(broker.all_positions()))
    for k in range(50):
        eng.on_timer(t + k + 1)
    pnl = [r.realized_pnl for r in eng.reports]
    worst = min(pnl, default=0.0)
    return StressResult(name, len(pnl), sum(pnl), worst, max_pos, [r.close_reason for r in eng.reports],
                        len(broker.all_positions()), planned, planned > 0 and -worst > planned)


def default_scenarios(cfg: StrategyConfig | None = None) -> list[StressResult]:
    cfg = cfg or StrategyConfig(cooldown_sec=0)
    p0 = 1.1000
    D = 0.0020
    out = []
    flat = [p0] * 5
    out.append(run_scenario("sideways_oscillation", flat + _path_sideways(p0, D, 30), cfg, {3: Side.BUY}))
    out.append(run_scenario("strong_trend_down", flat + _ramp(p0, p0 - 0.02, 400), cfg, {3: Side.BUY}))
    out.append(run_scenario("strong_trend_after_3_legs",
                            flat + _ramp(p0, p0 - D - 0.0001, 40) + _ramp(p0 - D, p0 + 0.0001, 40) + _ramp(p0, p0 - 0.02, 400),
                            cfg, {3: Side.BUY}))
    out.append(run_scenario("sudden_reversal",
                            flat + _ramp(p0, p0 + 0.004, 40) + _ramp(p0 + 0.004, p0 - 0.01, 20) + [p0 - 0.01] * 50,
                            cfg, {3: Side.BUY}))
    gap_path = flat + _ramp(p0, p0 - D - 0.0001, 40) + [p0 + 0.030] * 50
    out.append(run_scenario("gap_through_stop", gap_path, cfg, {3: Side.BUY}, gaps={45: 172800.0}))
    sp = {i: 0.0030 for i in range(3, 8)}
    out.append(run_scenario("spread_spike_at_signal", flat + [p0] * 5 + _ramp(p0, p0 + 0.01, 100), cfg,
                            {4: Side.BUY}, spreads=sp))
    out.append(run_scenario("insufficient_margin", flat + _ramp(p0, p0 - 0.01, 200), replace(cfg, min_margin_level_pct=100),
                            {3: Side.BUY}, leverage=2.0))
    ai = _ToggleAI()
    out.append(run_scenario("ai_outage_mid_basket", flat + _path_sideways(p0, D, 3) + _ramp(p0, p0 - 0.02, 300),
                            replace(cfg, ai_mode=AIMode.FILTER), {3: Side.BUY, 300: Side.BUY}, ai=ai, ai_down=(10, 10**9)))
    out.append(run_scenario("vps_restart_mid_basket", flat + _path_sideways(p0, D, 2) + _ramp(p0, p0 + 0.02, 300),
                            cfg, {3: Side.BUY}, restart_at={60, 61, 200}))
    out.append(run_scenario("duplicate_signals", flat + _ramp(p0, p0 + 0.01, 200), cfg,
                            {3: Side.BUY, 4: Side.BUY, 5: Side.SELL, 6: Side.BUY}))

    def partial(b):
        b.faults.partial_ratio = 0.5

    out.append(run_scenario("partial_fill_leg2", flat + _ramp(p0, p0 - 0.012, 200), cfg, {3: Side.BUY},
                            faults_at={10: partial}))

    def close_fail(b):
        b.faults.close_failures = 25

    out.append(run_scenario("failed_basket_close", flat + _ramp(p0, p0 + 0.01, 100) + [p0 + 0.01] * 100, cfg,
                            {3: Side.BUY}, faults_at={4: close_fail}))
    return out


def format_stress(results: list[StressResult]) -> str:
    hdr = f"{'scenario':28s} {'baskets':>7s} {'realized':>9s} {'worst':>9s} {'maxpos':>6s} {'open':>4s} {'>risk':>5s}  reasons"
    lines = [hdr, "-" * len(hdr)]
    for r in results:
        lines.append(f"{r.name:28s} {r.baskets:7d} {r.realized:9.2f} {r.worst_basket:9.2f} {r.max_positions_seen:6d} "
                     f"{r.final_open_positions:4d} {str(r.exceeded_planned_risk):>5s}  {','.join(r.close_reasons)}")
    return "\n".join(lines)
