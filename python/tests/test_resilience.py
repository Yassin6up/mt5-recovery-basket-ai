"""Failures, restarts, guards, AI outage, kill switch."""

import pytest

from conftest import SPREAD, Harness
from lrb.core.config import AIMode, LegApprovalMode, StrategyConfig
from lrb.core.engine import Engine, KillMode, LegState, Phase
from lrb.core.types import AIDecision, Side, SymbolSpec
from lrb.research.simbroker import SimBroker


def open_buy(h, bid=1.1):
    h.tick(bid)
    h.tick(bid, signal=Side.BUY)
    return h.engine.basket


class StubAI:
    def __init__(self, entry="ALLOW", leg="ALLOW", ttl=60, direction=None, fail=False, data_age=0):
        self.entry, self.leg, self.ttl, self.direction, self.fail, self.data_age = entry, leg, ttl, direction, fail, data_age
        self.calls = 0

    def _d(self, ctx, decision, kind):
        self.calls += 1
        if self.fail:
            return None  # timeout / invalid JSON / outage are all surfaced as "no valid decision"
        t = ctx["time"]
        return AIDecision("r", decision, self.direction or ctx["proposed_direction"], "stub", t - self.data_age,
                          t + self.ttl, ("TEST",), kind, ctx.get("leg_index", 0))

    def entry_decision(self, ctx):
        return self._d(ctx, self.entry, "ENTRY")

    def leg_decision(self, ctx):
        return self._d(ctx, self.leg, "RECOVERY_LEG")


def test_failed_close_keeps_closing_and_blocks_entries(h):
    b = open_buy(h)
    h.tick(b.levels.lower)
    h.broker.faults.close_failures = 4
    h.ramp(b.levels.lower, b.levels.lower - 0.0120, 100)
    # the first attempts fail; the engine keeps retrying on later ticks
    assert h.engine.reports, "basket should eventually close"
    rep = h.engine.reports[-1]
    assert rep.close_reason == "PROFIT_TARGET"
    assert rep.realized_pnl != pytest.approx(rep.trigger_pnl)  # realized differs from trigger value
    assert any(e["event"] == "CLOSE_FAILED" for e in h.engine.events)


def test_closing_state_prohibits_entries():
    h = Harness()
    b = open_buy(h)
    h.broker.faults.market_closed = True
    h.engine.kill(KillMode.FLATTEN)
    h.tick(1.1, signal=Side.BUY)
    assert h.engine.phase is Phase.CLOSING
    assert len(h.broker.all_positions()) == 1
    h.engine.kill(KillMode.NONE)
    h.tick(1.1, signal=Side.BUY)  # still closing => no new basket
    assert len(h.broker.all_positions()) == 1
    h.broker.faults.market_closed = False
    h.tick(1.1)
    assert h.broker.all_positions() == [] and h.engine.phase is Phase.FLAT


def test_restart_with_persisted_state_continues(h):
    b = open_buy(h)
    h.tick(b.levels.lower)
    saved = h.engine.to_json()
    e2 = Engine(h.cfg, h.broker)
    e2.load_json(saved)
    assert e2.phase is Phase.RECONCILE
    h.engine = e2
    h.tick(b.levels.lower + 0.0001)
    assert e2.phase is Phase.ACTIVE and len(e2.basket.filled_legs()) == 2
    h.ramp(b.levels.lower + 0.0001, b.levels.upper - SPREAD, 20)
    assert len(e2.basket.filled_legs()) == 3  # continues the progression after restart


def test_restart_with_empty_db_adopts_positions_and_does_not_add(h):
    b = open_buy(h)
    h.tick(b.levels.lower)
    e2 = Engine(h.cfg, h.broker)  # local state lost
    h.engine = e2
    h.tick(b.levels.lower + 0.0002, signal=Side.SELL)
    assert e2.basket is not None and e2.basket.orphan
    assert not e2.basket.adding_allowed and e2.basket.block_reason == "ORPHAN_ADOPTED"
    assert len(h.broker.all_positions()) == 2  # no new basket opened on top
    h.ramp(b.levels.lower, b.levels.lower - 0.0140, 200)
    assert e2.reports[-1].close_reason == "PROFIT_TARGET"


def test_positions_closed_while_offline(h):
    b = open_buy(h)
    saved = h.engine.to_json()
    for p in h.broker.all_positions():
        h.broker.force_close(p.ticket)
    e2 = Engine(h.cfg, h.broker)
    e2.load_json(saved)
    h.engine = e2
    h.tick(1.1)
    assert e2.reports[-1].close_reason == "CLOSED_WHILE_OFFLINE"


def test_external_close_triggers_emergency_close(h):
    b = open_buy(h)
    h.tick(b.levels.lower)
    h.broker.force_close(b.legs[0].ticket)  # e.g. broker stop-out or manual close of one leg
    h.tick(b.levels.lower + 0.00005)
    assert h.engine.reports[-1].close_reason == "EMERGENCY_EXTERNAL_CLOSE"
    assert h.broker.all_positions() == []


def test_unexpected_position_with_our_magic(h):
    b = open_buy(h)
    h.broker.open_foreign(Side.SELL, 0.05, magic=h.cfg.magic)
    h.tick(1.1)
    assert h.engine.reports[-1].close_reason == "EMERGENCY_UNEXPECTED_POSITION"
    assert h.broker.all_positions() == []


def test_ai_outage_blocks_entries_but_existing_basket_is_managed():
    cfg = StrategyConfig(cooldown_sec=0, ai_mode=AIMode.FILTER)
    ai = StubAI()
    h = Harness(cfg, ai=ai)
    b = open_buy(h)
    assert b is not None
    ai.fail = True  # service goes down
    h.tick(b.levels.lower)
    h.ramp(b.levels.lower, b.levels.lower - 0.0120, 200)
    assert h.engine.reports[-1].close_reason == "PROFIT_TARGET"
    h.tick(1.09, signal=Side.BUY)
    assert h.engine.basket is None
    assert h.engine.events[-1]["reason"] == "AI_UNAVAILABLE"


@pytest.mark.parametrize("ai,why", [
    (StubAI(entry="BLOCK"), "AI_BLOCK"),
    (StubAI(entry="WAIT"), "AI_WAIT"),
    (StubAI(ttl=-1), "AI_EXPIRED"),
    (StubAI(data_age=5000), "AI_STALE"),
    (StubAI(direction="SELL"), "AI_DIRECTION_MISMATCH"),
])
def test_ai_entry_decision_validation(ai, why):
    h = Harness(StrategyConfig(cooldown_sec=0, ai_mode=AIMode.FILTER), ai=ai)
    h.tick(1.1)
    h.tick(1.1, signal=Side.BUY)
    assert h.engine.basket is None
    assert h.engine.events[-1]["reason"] == why


def test_ai_blocked_recovery_leg_leaves_basket_exposed_with_exits():
    ai = StubAI(leg="BLOCK")
    cfg = StrategyConfig(cooldown_sec=0, ai_mode=AIMode.FILTER, leg_approval=LegApprovalMode.REQUIRED)
    h = Harness(cfg, ai=ai)
    b = open_buy(h)
    h.tick(b.levels.lower)
    assert b.legs[1].state is LegState.SKIPPED and b.block_reason == "AI_LEG_BLOCK"
    assert len(h.broker.all_positions()) == 1
    h.ramp(b.levels.lower, b.levels.stop_low - 0.0001, 40)
    assert h.engine.reports[-1].close_reason in ("PRICE_STOP", "BASKET_LOSS_LIMIT")


def _account_loss_scenario(cfg):
    """Our basket is small; a MANUAL position (other magic) drives the account loss."""
    h = Harness(cfg)
    h.tick(1.1)
    manual = h.broker.open_foreign(Side.BUY, 1.0, magic=0)
    b = open_buy(h)
    assert b is not None
    h.ramp(1.1, 1.0985, 30)  # manual position loses ~150 = 1.5% of equity
    return h, manual


def test_daily_loss_halt_and_next_day_clear():
    h, manual = _account_loss_scenario(StrategyConfig(cooldown_sec=0, daily_loss_pct=1.0))
    assert h.engine.reports[-1].close_reason == "DAILY_LOSS_LIMIT"
    assert h.engine.guard.daily_halt
    assert [p.ticket for p in h.broker.all_positions()] == [manual]  # manual trade untouched
    h.tick(1.0985, signal=Side.BUY)
    assert h.engine.basket is None
    h.tick(1.0985, dt=86400)
    assert not h.engine.guard.daily_halt


def test_drawdown_halt_requires_manual_reset():
    h, manual = _account_loss_scenario(StrategyConfig(cooldown_sec=0, max_drawdown_pct=1.0, daily_loss_pct=5))
    assert h.engine.reports[-1].close_reason == "ACCOUNT_DRAWDOWN_LIMIT"
    h.broker.force_close(manual)
    h.tick(1.0985, dt=86400 * 2, signal=Side.BUY)
    assert h.engine.basket is None and h.engine.guard.dd_halt
    h.engine.reset_drawdown_halt()
    h.tick(1.0985, signal=Side.BUY)
    assert h.engine.basket is not None


def test_kill_switch_halt_keeps_exits_flatten_closes(h):
    b = open_buy(h)
    h.engine.kill(KillMode.HALT)
    h.tick(b.levels.lower)
    assert b.legs[1].state is LegState.PLANNED  # no new legs under HALT
    h.engine.kill(KillMode.FLATTEN)
    h.tick(b.levels.lower)
    assert h.engine.reports[-1].close_reason == "KILL_SWITCH" and h.broker.all_positions() == []


def test_demo_only_refuses_live_and_netting_accounts():
    br = SimBroker(SymbolSpec(), is_demo=False, max_slippage_points=0)
    h = Harness(broker=br)
    h.tick(1.1)
    h.tick(1.1, signal=Side.BUY)
    assert h.engine.events[-1]["reason"] == "DEMO_ONLY_LIVE_ACCOUNT"
    br = SimBroker(SymbolSpec(), is_hedging=False, max_slippage_points=0)
    h = Harness(broker=br)
    h.tick(1.1)
    h.tick(1.1, signal=Side.BUY)
    assert h.engine.events[-1]["reason"] == "ACCOUNT_NOT_HEDGING"


def test_news_filter_fails_safe_without_calendar():
    h = Harness(StrategyConfig(cooldown_sec=0, news_filter_enabled=True))
    h.tick(1.1)
    h.tick(1.1, signal=Side.BUY, news_ok=None)
    assert h.engine.events[-1]["reason"] == "NEWS_DATA_UNAVAILABLE"


def test_stale_price_blocks_entry(h):
    h.tick(1.1)
    from lrb.core.signal import SignalSnapshot
    from lrb.core.types import Tick
    tk = Tick(h.t, 1.1, 1.1001)
    h.engine.on_tick(tk, SignalSnapshot(99, h.t, Side.BUY, 0.002, 0, 0, "x"), now=h.t + 60)
    assert h.engine.events[-1]["reason"] == "STALE_PRICE"


def test_stop_out_by_broker_is_detected():
    cfg = StrategyConfig(cooldown_sec=0, min_margin_level_pct=101, emergency_margin_level_pct=1,
                         min_free_margin_after_pct=0, risk_pct=5, max_gross_lots=50, max_net_lots=50,
                         daily_loss_pct=50, max_drawdown_pct=50)
    br = SimBroker(SymbolSpec(), balance=10_000, leverage=500, max_slippage_points=0, stop_out_level_pct=95)
    h = Harness(cfg, br, atr=0.0002)
    b = open_buy(h)
    assert b is not None
    h.ramp(1.1, 1.0950, 60)
    assert br.stop_outs >= 1 or h.engine.reports
