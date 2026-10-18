"""Recovery state machine: leg sequencing, triggers, duplicates, exits."""

import pytest

from conftest import SPREAD, Harness
from lrb.core.config import StrategyConfig
from lrb.core.engine import LegState, Phase
from lrb.core.types import Side


def open_buy(h: Harness, bid=1.1000):
    h.tick(bid)
    h.tick(bid, signal=Side.BUY)
    return h.engine.basket


def states(b):
    return [l.state for l in b.legs]


def test_buy_progression_and_leg_prices(h):
    b = open_buy(h)
    assert b.phase is Phase.ACTIVE
    up, lo = b.levels.upper, b.levels.lower
    assert up == pytest.approx(1.1000 + SPREAD)  # leg 1 fills at the ask => upper anchor
    assert up - lo == pytest.approx(0.0020)
    # bid falls to (but not through) lower: no trigger
    h.ramp(1.1000, lo + 0.00001, 20)
    assert states(b)[1] is LegState.PLANNED
    h.tick(lo)  # bid touches lower => SELL 2q at the bid
    assert b.legs[1].state is LegState.FILLED and b.legs[1].side is Side.SELL
    assert b.legs[1].fill_price == pytest.approx(lo)
    assert b.legs[1].filled_volume == pytest.approx(2 * b.legs[0].filled_volume)
    # ask must come back up to upper for leg 3 (BUY 3q at the ask)
    h.ramp(lo, up - SPREAD - 0.00001, 20)
    assert b.legs[2].state is LegState.PLANNED
    h.tick(up - SPREAD)
    assert b.legs[2].state is LegState.FILLED and b.legs[2].side is Side.BUY
    assert b.legs[2].filled_volume == pytest.approx(3 * b.legs[0].filled_volume)
    assert not b.adding_allowed and b.block_reason == "MAX_LEGS_REACHED"
    assert len(h.broker.positions("EURUSD", h.cfg.magic)) == 3


def test_sell_progression_mirrors(h):
    h.tick(1.1000)
    h.tick(1.1000, signal=Side.SELL)
    b = h.engine.basket
    assert b.levels.lower == pytest.approx(1.1000)  # leg 1 SELL fills at bid => lower anchor
    h.ramp(1.1000, b.levels.upper - SPREAD, 30)  # ask reaches upper
    assert b.legs[1].state is LegState.FILLED and b.legs[1].side is Side.BUY
    h.ramp(b.levels.upper - SPREAD, b.levels.lower, 30)
    assert b.legs[2].state is LegState.FILLED and b.legs[2].side is Side.SELL
    assert [l.side for l in b.legs] == [Side.SELL, Side.BUY, Side.SELL]


def test_no_duplicate_leg_on_repeated_crossings(h):
    b = open_buy(h)
    lo = b.levels.lower
    for _ in range(10):  # chatter around the lower boundary
        h.tick(lo - 0.00002)
        h.tick(lo + 0.00002)
    assert sum(1 for l in b.legs if l.is_filled) == 2
    assert len(h.broker.positions("EURUSD", h.cfg.magic)) == 2


def test_duplicate_signal_same_bar_opens_one_basket(h):
    h.tick(1.1)
    h.tick(1.1, signal=Side.BUY)
    bar = h.bar
    from lrb.core.signal import SignalSnapshot
    from lrb.core.types import Tick
    for _ in range(3):
        h.t += 1
        tk = Tick(h.t, 1.1, 1.1 + SPREAD)
        h.broker.set_tick(tk)
        h.engine.on_tick(tk, SignalSnapshot(bar, h.t, Side.BUY, 0.002, 0, 0, "dup"))
    assert len(h.broker.all_positions()) == 1


def test_prolonged_oscillation_never_exceeds_max_positions(h):
    b = open_buy(h)
    up, lo = b.levels.upper, b.levels.lower
    for _ in range(25):
        h.ramp(up - SPREAD, lo, 10)
        h.ramp(lo, up - SPREAD, 10)
    assert len(h.broker.all_positions()) <= h.cfg.max_positions
    # basket either still open with 3 legs or closed by a deterministic rule
    assert h.engine.basket is None or len(h.engine.basket.filled_legs()) == 3


def test_profit_target_closes_all_including_losers(h):
    b = open_buy(h)
    lo = b.levels.lower
    h.tick(lo)  # SELL 2q now open; price then falls => SELL wins, BUY loses
    h.ramp(lo, lo - 0.0120, 200)
    assert h.engine.basket is None
    rep = h.engine.reports[-1]
    assert rep.close_reason == "PROFIT_TARGET"
    assert rep.legs_filled == 2
    assert h.broker.all_positions() == []
    assert rep.realized_pnl > 0
    assert rep.realized_pnl == pytest.approx(h.broker.balance - 10_000)


def test_trend_hits_price_stop_within_budget(h):
    b = open_buy(h)
    up, lo = b.levels.upper, b.levels.lower
    h.tick(lo)
    h.ramp(lo, up - SPREAD, 20)
    h.ramp(up - SPREAD, b.levels.stop_low - 0.0005, 200)  # strong down trend after 3 legs
    rep = h.engine.reports[-1]
    assert rep.close_reason in ("PRICE_STOP", "BASKET_LOSS_LIMIT")
    assert rep.realized_pnl < 0
    assert -rep.realized_pnl <= rep.risk_money * 1.02


def test_gap_beyond_trigger_stops_adding_but_keeps_exits(h):
    b = open_buy(h)
    lo = b.levels.lower
    h.tick(lo - 0.0010)  # gap 100 points through the lower boundary (limit 50)
    assert b.legs[1].state is LegState.SKIPPED and b.block_reason == "GAP_BEYOND_TRIGGER"
    h.ramp(lo - 0.0010, b.levels.stop_low - 0.0001, 30)
    assert h.engine.reports[-1].close_reason in ("PRICE_STOP", "BASKET_LOSS_LIMIT")


def test_gap_through_stop_can_exceed_planned_loss():
    h = Harness(StrategyConfig(cooldown_sec=0, max_trigger_slippage_points=10))
    b = open_buy(h)
    h.tick(b.levels.lower)
    planned = h.engine.reports  # noqa
    h.tick(b.levels.stop_high + 0.0200)  # weekend-style gap far beyond the active stop
    rep = h.engine.reports[-1]
    assert rep.close_reason in ("PRICE_STOP", "BASKET_LOSS_LIMIT")
    assert -rep.realized_pnl > rep.risk_money  # documented limitation: a software stop is not a guarantee


def test_spread_spike_blocks_entry_and_defers_leg(h):
    h.tick(1.1)
    h.tick(1.1, spread=0.0005, signal=Side.BUY)
    assert h.engine.basket is None
    assert h.engine.events[-1]["reason"] == "SPREAD_TOO_WIDE"
    b = open_buy(h)
    lo = b.levels.lower
    h.tick(lo, spread=0.0006)  # 60 points > max_leg_spread_points (40)
    assert b.legs[1].state is LegState.PLANNED
    assert any(e["event"] == "LEG_DEFERRED_SPREAD" for e in h.engine.events)
    h.tick(lo)
    assert b.legs[1].is_filled


def test_insufficient_margin_stops_adding():
    from lrb.core.types import SymbolSpec
    from lrb.research.simbroker import SimBroker
    cfg = StrategyConfig(cooldown_sec=0, min_margin_level_pct=100)
    br = SimBroker(SymbolSpec(), leverage=1.0, max_slippage_points=0)
    h = Harness(cfg, br)
    h.tick(1.1)
    h.tick(1.1, signal=Side.BUY)
    assert h.engine.basket is None
    assert h.engine.events[-1]["reason"] in ("PROJECTED_MARGIN_LEVEL", "INSUFFICIENT_FREE_MARGIN")


def test_max_duration_closes(h):
    b = open_buy(h)
    h.tick(1.1000, dt=h.cfg.max_basket_duration_sec + 1)
    assert h.engine.reports[-1].close_reason == "MAX_DURATION"


def test_partial_fill_stops_adding(h):
    b = open_buy(h)
    h.broker.faults.partial_ratio = 0.5
    h.tick(b.levels.lower)
    assert b.legs[1].state is LegState.PARTIAL
    assert not b.adding_allowed and b.block_reason == "PARTIAL_FILL"
    assert b.legs[2].state is LegState.SKIPPED


def test_timeout_is_reconciled_without_duplicate_send(h):
    h.tick(1.1)
    h.broker.faults.timeout_sends = 1
    h.tick(1.1, signal=Side.BUY)
    b = h.engine.basket
    assert b.phase is Phase.OPENING and b.legs[0].state is LegState.SENT
    h.tick(1.1)
    assert b.phase is Phase.ACTIVE  # acknowledged via reconciliation of actual positions
    assert b.legs[0].state is LegState.FILLED
    assert len(h.broker.all_positions()) == 1
    assert any(e.get("late_ack") for e in h.engine.events if e["event"] == "LEG_FILLED")


def test_rejected_leg_retried_then_abandoned(h):
    b = open_buy(h)
    h.broker.faults.reject_sends = 5
    h.tick(b.levels.lower)
    h.tick(b.levels.lower - 0.00001)
    assert b.legs[1].state is LegState.FAILED
    assert b.block_reason == "LEG_SEND_FAILED"
    assert len(h.broker.all_positions()) == 1


def test_rejected_leg1_aborts_basket_without_stuck_state(h):
    h.tick(1.1)
    h.broker.faults.reject_sends = 1
    h.tick(1.1, signal=Side.BUY)
    assert h.engine.basket is None and h.engine.phase is Phase.FLAT
    assert h.broker.all_positions() == []
    assert any(e["event"] == "BASKET_ABORTED" for e in h.engine.events)


def test_manual_and_foreign_positions_never_touched(h):
    manual = h.broker.open_foreign(Side.SELL, 0.5, magic=0) if h.broker._tick else None
    h.tick(1.1)
    manual = h.broker.open_foreign(Side.SELL, 0.5, magic=0)
    other = h.broker.open_foreign(Side.BUY, 0.3, magic=999)
    b = open_buy(h)
    h.tick(b.levels.lower)
    h.ramp(b.levels.lower, b.levels.lower - 0.0120, 200)  # profit target for our basket
    assert h.engine.reports[-1].close_reason == "PROFIT_TARGET"
    tickets = {p.ticket for p in h.broker.all_positions()}
    assert {manual, other} <= tickets
