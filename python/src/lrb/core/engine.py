"""Limited Recovery Basket state machine (reference implementation of the EA logic).

Per-tick evaluation order (identical in the EA):

1. Account guards: day rollover, equity high-water mark, daily-loss / drawdown halts.
2. Startup reconciliation (once) against actual broker positions and deal history.
3. Position reconciliation: acknowledge pending legs, detect external closes/unknowns.
4. Deterministic exits (risk exits first, then target / stops / duration / emergency).
5. CLOSING: keep closing every remaining basket position until none are left.
6. ACTIVE: recovery-leg trigger detection (armed crossing), filters, then send.
7. FLAT: baseline signal -> filters -> sizing -> margin -> (optional AI) -> leg 1.

Exits never wait for the AI: AI decisions are only *read* from a cache by the engine.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol

from .accounting import BasketPnL, basket_pnl, final_realized
from .broker import Broker
from .config import AIMode, LegApprovalMode, StrategyConfig
from .signal import SignalSnapshot
from .sizing import Levels, SizingResult, compute_base_lot, leg_sides, levels_from_anchor
from .types import AccountState, AIDecision, OrderResult, Position, Retcode, Side, Tick

STATE_VERSION = 1


class Phase(str, Enum):
    RECONCILE = "RECONCILE"
    FLAT = "FLAT"
    OPENING = "OPENING"
    ACTIVE = "ACTIVE"
    CLOSING = "CLOSING"


class LegState(str, Enum):
    PLANNED = "PLANNED"
    SENT = "SENT"  # request sent, acknowledgement pending (TIMEOUT / unknown outcome)
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"  # filled with less than planned volume; counts as filled
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class KillMode(str, Enum):
    NONE = "NONE"
    HALT = "HALT"  # no new baskets / legs; existing basket keeps deterministic exits
    FLATTEN = "FLATTEN"  # close the basket now and halt


class DecisionProvider(Protocol):
    def entry_decision(self, ctx: dict[str, Any]) -> AIDecision | None: ...

    def leg_decision(self, ctx: dict[str, Any]) -> AIDecision | None: ...


@dataclass
class LegRecord:
    index: int  # 1-based
    side: Side
    planned_volume: float
    state: LegState = LegState.PLANNED
    attempts: int = 0
    ticket: int = 0
    filled_volume: float = 0.0
    fill_price: float = 0.0
    sent_time: float = 0.0
    request_id: str = ""
    closed: bool = False

    @property
    def is_filled(self) -> bool:
        return self.state in (LegState.FILLED, LegState.PARTIAL)


@dataclass
class BasketState:
    basket_id: str
    direction: Side
    phase: Phase
    legs: list[LegRecord]
    start_time: float
    start_equity: float
    risk_money: float
    target_money: float
    levels: Levels | None = None
    adding_allowed: bool = True
    block_reason: str = ""
    armed: bool = False
    position_ids: list[int] = field(default_factory=list)
    close_reason: str = ""
    close_trigger_pnl: float = 0.0
    close_started: float = 0.0
    last_close_attempt: float = 0.0
    close_attempts: int = 0
    orphan: bool = False
    sizing_note: str = ""

    def filled_legs(self) -> list[LegRecord]:
        return [l for l in self.legs if l.is_filled]

    def pending_leg(self) -> LegRecord | None:
        return next((l for l in self.legs if l.state is LegState.SENT), None)


@dataclass
class GuardState:
    day_key: int = -1
    sod_equity: float = 0.0
    peak_equity: float = 0.0
    daily_halt: bool = False
    dd_halt: bool = False  # requires manual reset
    kill: KillMode = KillMode.NONE
    exec_errors: list[float] = field(default_factory=list)
    cooldown_until: float = 0.0
    last_signal_bar: float = -1.0


@dataclass
class BasketReport:
    basket_id: str
    direction: str
    start_time: float
    end_time: float
    legs_filled: int
    max_legs: int
    close_reason: str
    trigger_pnl: float
    realized_pnl: float
    start_equity: float
    risk_money: float
    target_money: float
    max_floating_loss: float
    max_gross_lots: float
    orphan: bool


class Engine:
    def __init__(
        self,
        cfg: StrategyConfig,
        broker: Broker,
        ai: DecisionProvider | None = None,
        log: Callable[[dict[str, Any]], None] | None = None,
    ):
        cfg.validate()
        self.cfg = cfg
        self.broker = broker
        self.ai = ai
        self._log_sink = log
        self.phase = Phase.RECONCILE
        self.basket: BasketState | None = None
        self.guard = GuardState()
        self.prev_tick: Tick | None = None
        self.reports: list[BasketReport] = []
        self.events: list[dict[str, Any]] = []
        self._max_float_loss = 0.0
        self._max_gross = 0.0
        self._seq = 0

    # ------------------------------------------------------------------ logging
    def _event(self, t: float, kind: str, **data: Any) -> None:
        e = {"t": t, "event": kind, **data}
        self.events.append(e)
        if len(self.events) > 5000:
            del self.events[:1000]
        if self._log_sink:
            self._log_sink(e)

    # ------------------------------------------------------------------ public API
    def kill(self, mode: KillMode, t: float = 0.0) -> None:
        self.guard.kill = mode
        self._event(t, "KILL_SWITCH", mode=mode.value)

    def reset_drawdown_halt(self, t: float = 0.0) -> None:
        """Manual operator action after reviewing a drawdown halt."""
        acct = self.broker.account()
        self.guard.dd_halt = False
        self.guard.peak_equity = acct.equity
        self._event(t, "DD_HALT_RESET", equity=acct.equity)

    @property
    def entries_blocked_reason(self) -> str | None:
        g = self.guard
        if self.phase is Phase.RECONCILE:
            return "RECONCILING"
        if g.kill is not KillMode.NONE:
            return "KILL_SWITCH"
        if g.dd_halt:
            return "DRAWDOWN_HALT"
        if g.daily_halt:
            return "DAILY_LOSS_HALT"
        return None

    def on_tick(
        self,
        tick: Tick,
        signal: SignalSnapshot | None = None,
        now: float | None = None,
        news_ok: bool | None = True,
    ) -> None:
        now = tick.time if now is None else now
        acct = self.broker.account()
        self._update_guards(now, acct)
        if self.phase is Phase.RECONCILE:
            self.startup_reconcile(now)
        b = self.basket
        if b is not None and b.phase in (Phase.OPENING, Phase.ACTIVE, Phase.CLOSING):
            self._reconcile_positions(now)
        b = self.basket
        if b is not None and b.phase is Phase.ACTIVE:
            pnl = self.pnl()
            self._track_extremes(pnl)
            reason = self._exit_reason(tick, pnl, acct, now)
            if reason:
                self._begin_close(now, reason, pnl)
        b = self.basket
        if b is not None and b.phase is Phase.CLOSING:
            self._process_close(now)
        elif b is not None and b.phase is Phase.ACTIVE:
            self._maybe_add_leg(tick, acct, now)
        if self.basket is None and self.phase is Phase.FLAT:
            self._maybe_open(tick, acct, signal, now, news_ok)
        self.prev_tick = tick

    def on_timer(self, now: float) -> None:
        """Timer path (EA OnTimer): guards and close retries keep running without ticks."""
        acct = self.broker.account()
        self._update_guards(now, acct)
        if self.basket is not None and self.basket.phase is Phase.CLOSING:
            self._process_close(now)

    # ------------------------------------------------------------------ guards
    def _update_guards(self, now: float, acct: AccountState) -> None:
        g, cfg = self.guard, self.cfg
        day_key = int(math.floor((now - cfg.day_rollover_utc_hour * 3600) / 86400))
        if day_key != g.day_key:
            if g.day_key != -1 and g.daily_halt:
                self._event(now, "DAILY_HALT_CLEARED")
            g.day_key = day_key
            g.sod_equity = acct.equity
            g.daily_halt = False
        g.peak_equity = max(g.peak_equity, acct.equity)
        if not g.daily_halt and acct.equity <= g.sod_equity * (1 - cfg.daily_loss_pct / 100.0):
            g.daily_halt = True
            self._event(now, "DAILY_LOSS_HALT", equity=acct.equity, sod_equity=g.sod_equity)
        if not g.dd_halt and acct.equity <= g.peak_equity * (1 - cfg.max_drawdown_pct / 100.0):
            g.dd_halt = True
            self._event(now, "DRAWDOWN_HALT", equity=acct.equity, peak=g.peak_equity)
        g.exec_errors = [t for t in g.exec_errors if now - t < 3600]

    def _risk_room(self, acct: AccountState) -> float:
        g, cfg = self.guard, self.cfg
        daily_room = g.sod_equity * cfg.daily_loss_pct / 100.0 - max(0.0, g.sod_equity - acct.equity)
        dd_room = g.peak_equity * cfg.max_drawdown_pct / 100.0 - max(0.0, g.peak_equity - acct.equity)
        return min(daily_room, dd_room)

    # ------------------------------------------------------------------ accounting
    def _positions(self) -> list[Position]:
        return self.broker.positions(self.cfg.symbol, self.cfg.magic)

    def pnl(self) -> BasketPnL:
        b = self.basket
        assert b is not None
        ids = set(b.position_ids)
        return basket_pnl(
            self._positions(),
            self.broker.position_profit,
            self.broker.deals(b.start_time - 60),
            ids,
            self.broker.spec(),
            self.cfg.commission_per_lot_side,
            self.cfg.slippage_allowance_points,
        )

    def _track_extremes(self, pnl: BasketPnL) -> None:
        self._max_float_loss = min(self._max_float_loss, pnl.net_if_closed_now_ex_costs)
        self._max_gross = max(self._max_gross, pnl.open_volume)

    def _net_exposure(self) -> float:
        return sum(p.side.value * p.volume for p in self._positions())

    def _gross_exposure(self) -> float:
        return sum(p.volume for p in self._positions())

    # ------------------------------------------------------------------ reconciliation
    def startup_reconcile(self, now: float) -> None:
        """Rebuild the view from actual broker positions. Never trusts an empty local state."""
        positions = self._positions()
        acct = self.broker.account()
        b = self.basket
        if b is None:
            if positions:
                self._adopt_orphan(now, positions, acct)
                self.phase = Phase.ACTIVE
            else:
                self.phase = Phase.FLAT
            self._event(now, "RECONCILED", positions=len(positions), local_state=False)
            return
        if not positions and b.phase in (Phase.ACTIVE, Phase.OPENING, Phase.CLOSING):
            had_fill = any(l.is_filled for l in b.legs) or b.pending_leg() is not None
            if had_fill:
                # Everything was closed while we were offline (broker stop, manual, our own close).
                b.close_reason = b.close_reason or "CLOSED_WHILE_OFFLINE"
                b.phase = Phase.CLOSING
                self.phase = Phase.CLOSING
                self._event(now, "RECONCILED", positions=0, local_state=True, note="closed while offline")
                return
            self.basket = None
            self.phase = Phase.FLAT
            self._event(now, "RECONCILED", positions=0, local_state=True, note="no fills; basket discarded")
            return
        self.phase = b.phase
        self._event(now, "RECONCILED", positions=len(positions), local_state=True, phase=b.phase.value)

    def _adopt_orphan(self, now: float, positions: list[Position], acct: AccountState) -> None:
        positions = sorted(positions, key=lambda p: (p.open_time, p.ticket))
        legs = []
        for i, p in enumerate(positions, start=1):
            legs.append(LegRecord(i, p.side, p.volume, LegState.FILLED, 1, p.ticket, p.volume, p.open_price, p.open_time))
        base = acct.balance
        b = BasketState(
            basket_id=f"ORPHAN-{int(now)}",
            direction=positions[0].side,
            phase=Phase.ACTIVE,
            legs=legs,
            start_time=positions[0].open_time,
            start_equity=base,
            risk_money=self.cfg.risk_pct / 100.0 * base,
            target_money=self.cfg.target_pct / 100.0 * base,
            levels=None,
            adding_allowed=False,
            block_reason="ORPHAN_ADOPTED",
            position_ids=[p.ticket for p in positions],
            orphan=True,
        )
        self.basket = b
        self._max_float_loss = 0.0
        self._max_gross = 0.0
        self._event(now, "ORPHAN_ADOPTED", tickets=b.position_ids)

    def _reconcile_positions(self, now: float) -> None:
        b = self.basket
        assert b is not None
        positions = self._positions()
        by_ticket = {p.ticket: p for p in positions}
        known = {l.ticket for l in b.legs if l.ticket}
        for p in positions:
            if p.ticket in known:
                continue
            pend = b.pending_leg()
            if pend is not None and pend.side is p.side and p.open_time >= pend.sent_time - 5:
                self._ack_leg(now, pend, p.ticket, p.volume, p.open_price, late=True)
                known.add(p.ticket)
                continue
            # A position with our magic/symbol that we cannot attribute -> deterministic emergency.
            b.position_ids.append(p.ticket)
            known.add(p.ticket)
            self._event(now, "UNEXPECTED_POSITION", ticket=p.ticket)
            if b.phase is not Phase.CLOSING:
                self._begin_close(now, "EMERGENCY_UNEXPECTED_POSITION", None)
        for leg in b.legs:
            if leg.is_filled and not leg.closed and leg.ticket not in by_ticket:
                leg.closed = True
                if b.phase is not Phase.CLOSING:
                    self._event(now, "EXTERNAL_CLOSE_DETECTED", ticket=leg.ticket, leg=leg.index)
                    self._begin_close(now, "EMERGENCY_EXTERNAL_CLOSE", None)
        pend = b.pending_leg()
        if pend is not None and now - pend.sent_time > self.cfg.pending_ack_timeout_sec:
            pend.state = LegState.FAILED
            self._exec_error(now, "LEG_ACK_TIMEOUT", leg=pend.index)
            b.adding_allowed = False
            b.block_reason = "LEG_ACK_TIMEOUT"
            if b.phase is Phase.OPENING:
                self._event(now, "BASKET_ABORTED", basket=b.basket_id)
                self.basket = None
                self.phase = Phase.FLAT

    def _ack_leg(self, now: float, leg: LegRecord, ticket: int, volume: float, price: float, late: bool = False) -> None:
        b = self.basket
        assert b is not None
        leg.ticket = ticket
        leg.filled_volume = volume
        leg.fill_price = price
        partial = volume + 1e-9 < leg.planned_volume
        leg.state = LegState.PARTIAL if partial else LegState.FILLED
        if ticket not in b.position_ids:
            b.position_ids.append(ticket)
        b.armed = False
        if partial and leg.index < len(b.legs):
            self._stop_adding(now, "PARTIAL_FILL", leg=leg.index)
        if leg.index == 1:
            b.levels = levels_from_anchor(b.direction, price, b.levels.distance if b.levels else 0.0, self.cfg.stop_distance_mult)
            b.phase = Phase.ACTIVE
            self.phase = Phase.ACTIVE
        if leg.index >= len(b.legs) and b.adding_allowed:
            b.adding_allowed = False
            b.block_reason = "MAX_LEGS_REACHED"
        self._rearm(b)
        self._event(now, "LEG_FILLED", basket=b.basket_id, leg=leg.index, side=leg.side.name,
                    volume=volume, price=price, partial=partial, late_ack=late)

    def _rearm(self, b: BasketState) -> None:
        """Arm the next trigger iff the current quote is strictly on the near side of it.

        A trigger fires only on a transition from armed (near side) to at/through the
        boundary, so a quote that is already beyond the boundary when a leg fills can not
        fire the next leg until price has come back and crossed again.
        """
        nxt = next((l for l in b.legs if l.state is LegState.PLANNED), None)
        if nxt is None or b.levels is None:
            b.armed = False
            return
        t = self.broker.last_tick()
        b.armed = t.ask < b.levels.upper if nxt.side is Side.BUY else t.bid > b.levels.lower

    # ------------------------------------------------------------------ exits
    def _exit_reason(self, tick: Tick, pnl: BasketPnL, acct: AccountState, now: float) -> str | None:
        b, g, cfg = self.basket, self.guard, self.cfg
        assert b is not None
        if g.kill is KillMode.FLATTEN:
            return "KILL_SWITCH"
        if g.daily_halt:
            return "DAILY_LOSS_LIMIT"
        if g.dd_halt:
            return "ACCOUNT_DRAWDOWN_LIMIT"
        if pnl.net <= -b.risk_money:
            return "BASKET_LOSS_LIMIT"
        if b.levels is not None and b.filled_legs():
            # Only the stop adverse to the current net exposure is active (flat => both).
            net = round(self._net_exposure(), 8)
            if net >= 0 and tick.bid <= b.levels.stop_low:
                return "PRICE_STOP"
            if net <= 0 and tick.ask >= b.levels.stop_high:
                return "PRICE_STOP"
        if acct.margin_level < cfg.emergency_margin_level_pct:
            return "EMERGENCY_MARGIN"
        if now - b.start_time >= cfg.max_basket_duration_sec:
            return "MAX_DURATION"
        if pnl.net >= b.target_money:
            return "PROFIT_TARGET"
        return None

    def _begin_close(self, now: float, reason: str, pnl: BasketPnL | None) -> None:
        b = self.basket
        assert b is not None
        if pnl is None:
            try:
                pnl = self.pnl()
            except Exception:  # pragma: no cover - defensive
                pnl = None
        b.phase = Phase.CLOSING
        self.phase = Phase.CLOSING
        b.adding_allowed = False
        b.block_reason = b.block_reason or "CLOSING"
        b.close_reason = reason
        b.close_trigger_pnl = pnl.net if pnl else 0.0
        b.close_started = now
        b.last_close_attempt = 0.0
        self._event(now, "CLOSE_BEGIN", basket=b.basket_id, reason=reason, trigger_pnl=b.close_trigger_pnl)

    def _process_close(self, now: float) -> None:
        b = self.basket
        assert b is not None
        positions = self._positions()
        if positions and now - b.last_close_attempt >= self.cfg.close_retry_sec:
            b.last_close_attempt = now
            b.close_attempts += 1
            for p in positions:
                if p.ticket not in b.position_ids:
                    b.position_ids.append(p.ticket)
                res = self.broker.close_position(p.ticket, self.cfg.deviation_points)
                if res.retcode not in (Retcode.DONE, Retcode.DONE_PARTIAL):
                    self._exec_error(now, "CLOSE_FAILED", ticket=p.ticket, retcode=res.retcode.value)
            positions = self._positions()
        if not positions:
            self._finalize(now)
        elif b.close_attempts and b.close_attempts % 10 == 0:
            self._event(now, "CLOSE_STILL_PENDING", remaining=len(positions), attempts=b.close_attempts)

    def _finalize(self, now: float) -> None:
        b = self.basket
        assert b is not None
        realized = final_realized(self.broker.deals(b.start_time - 60), set(b.position_ids))
        rep = BasketReport(
            basket_id=b.basket_id, direction=b.direction.name, start_time=b.start_time, end_time=now,
            legs_filled=len(b.filled_legs()), max_legs=len(b.legs), close_reason=b.close_reason,
            trigger_pnl=b.close_trigger_pnl, realized_pnl=realized, start_equity=b.start_equity,
            risk_money=b.risk_money, target_money=b.target_money, max_floating_loss=self._max_float_loss,
            max_gross_lots=self._max_gross, orphan=b.orphan,
        )
        self.reports.append(rep)
        self._event(now, "BASKET_CLOSED", **asdict(rep))
        self.basket = None
        self.phase = Phase.FLAT
        self.guard.cooldown_until = now + self.cfg.cooldown_sec
        self._max_float_loss = 0.0
        self._max_gross = 0.0

    # ------------------------------------------------------------------ recovery legs
    def _maybe_add_leg(self, tick: Tick, acct: AccountState, now: float) -> None:
        b, cfg = self.basket, self.cfg
        assert b is not None
        if not b.adding_allowed or b.levels is None or b.pending_leg() is not None:
            return
        blocked = self.entries_blocked_reason
        if blocked:
            return  # deterministic exits remain active; we simply do not add exposure
        nxt = next((l for l in b.legs if l.state is LegState.PLANNED), None)
        if nxt is None:
            self._stop_adding(now, "MAX_LEGS_REACHED")
            return
        lv, spec = b.levels, self.broker.spec()
        if nxt.side is Side.BUY:
            armed_now, fired, slip = tick.ask < lv.upper, tick.ask >= lv.upper, tick.ask - lv.upper
        else:
            armed_now, fired, slip = tick.bid > lv.lower, tick.bid <= lv.lower, lv.lower - tick.bid
        if not b.armed:
            if armed_now:
                b.armed = True
            return
        if not fired:
            return
        if slip > cfg.max_trigger_slippage_points * spec.point:
            self._stop_adding(now, "GAP_BEYOND_TRIGGER", leg=nxt.index, slip=slip)
            return
        if now - tick.time > cfg.max_tick_age_sec:
            return  # stale quote: wait; slippage rule above still applies on the next tick
        if tick.spread > cfg.max_leg_spread_points * spec.point:
            self._event(now, "LEG_DEFERRED_SPREAD", leg=nxt.index, spread=tick.spread)
            return
        why = self._leg_risk_check(nxt, tick, acct)
        if why:
            self._stop_adding(now, why, leg=nxt.index)
            return
        if cfg.leg_approval is LegApprovalMode.REQUIRED:
            ctx = self._leg_context(nxt, tick, acct)
            dec = self.ai.leg_decision(ctx) if self.ai else None
            ok, why = self._validate_decision(dec, now, "RECOVERY_LEG", nxt.side, leg_index=nxt.index)
            if not ok:
                # The basket stays exposed. We stop adding, keep deterministic exits, and do
                # NOT assume a later leg will rescue it.
                self._stop_adding(now, f"AI_LEG_{why}", leg=nxt.index)
                return
        self._send_leg(now, nxt, tick)

    def _stop_adding(self, now: float, reason: str, **data: Any) -> None:
        b = self.basket
        assert b is not None
        b.adding_allowed = False
        b.block_reason = reason
        for l in b.legs:
            if l.state is LegState.PLANNED:
                l.state = LegState.SKIPPED
        self._event(now, "STOP_ADDING", basket=b.basket_id, reason=reason, **data)

    def _leg_risk_check(self, leg: LegRecord, tick: Tick, acct: AccountState) -> str | None:
        cfg = self.cfg
        gross = self._gross_exposure() + leg.planned_volume
        net = self._net_exposure() + leg.side.value * leg.planned_volume
        if gross > cfg.max_gross_lots + 1e-9:
            return "MAX_GROSS_EXPOSURE"
        if abs(net) > cfg.max_net_lots + 1e-9:
            return "MAX_NET_EXPOSURE"
        price = tick.ask if leg.side is Side.BUY else tick.bid
        add_margin = self.broker.calc_margin(leg.side, leg.planned_volume, price)
        projected = acct.margin + add_margin
        if projected > 0 and acct.equity / projected * 100.0 < cfg.min_margin_level_pct:
            return "PROJECTED_MARGIN_LEVEL"
        if acct.equity - projected < cfg.min_free_margin_after_pct / 100.0 * acct.equity:
            return "INSUFFICIENT_FREE_MARGIN"
        return None

    def _send_leg(self, now: float, leg: LegRecord, tick: Tick) -> OrderResult:
        b, cfg = self.basket, self.cfg
        assert b is not None
        leg.attempts += 1
        leg.state = LegState.SENT
        leg.sent_time = now
        leg.request_id = f"{b.basket_id}:L{leg.index}:{leg.attempts}"
        comment = f"LRB|{b.basket_id[-12:]}|L{leg.index}"
        res = self.broker.send_market(leg.side, leg.planned_volume, cfg.magic, comment, cfg.deviation_points)
        self._event(now, "LEG_SENT", basket=b.basket_id, leg=leg.index, side=leg.side.name,
                    volume=leg.planned_volume, request_id=leg.request_id, retcode=res.retcode.value)
        if res.retcode in (Retcode.DONE, Retcode.DONE_PARTIAL) and res.filled_volume > 0:
            self._ack_leg(now, leg, res.position_ticket, res.filled_volume, res.price)
        elif res.retcode is Retcode.TIMEOUT:
            pass  # stays SENT; reconciliation will acknowledge or fail it
        else:
            leg.state = LegState.PLANNED
            self._exec_error(now, "LEG_SEND_FAILED", leg=leg.index, retcode=res.retcode.value)
            if leg.attempts >= cfg.leg_max_attempts:
                leg.state = LegState.FAILED
                if leg.index == 1:
                    self._event(now, "BASKET_ABORTED", basket=b.basket_id)
                    self.basket = None
                    self.phase = Phase.FLAT
                    self.guard.cooldown_until = now + 60
                else:
                    self._stop_adding(now, "LEG_SEND_FAILED", leg=leg.index)
        return res

    def _exec_error(self, now: float, kind: str, **data: Any) -> None:
        self.guard.exec_errors.append(now)
        self._event(now, kind, **data)

    # ------------------------------------------------------------------ new basket
    def _entry_filters(self, tick: Tick, acct: AccountState, now: float, news_ok: bool | None) -> str | None:
        cfg, spec = self.cfg, self.broker.spec()
        if cfg.demo_only and not acct.is_demo:
            return "DEMO_ONLY_LIVE_ACCOUNT"
        if not acct.is_demo and not cfg.allow_live:
            return "LIVE_NOT_ENABLED"
        if not acct.is_hedging:
            return "ACCOUNT_NOT_HEDGING"
        if now < self.guard.cooldown_until:
            return "COOLDOWN"
        if self._positions():
            return "EXISTING_POSITIONS"
        if now - tick.time > cfg.max_tick_age_sec:
            return "STALE_PRICE"
        if not self.broker.market_open(now):
            return "MARKET_CLOSED"
        if tick.spread > cfg.max_spread_points * spec.point:
            return "SPREAD_TOO_WIDE"
        if cfg.news_filter_enabled:
            if news_ok is None:
                return "NEWS_DATA_UNAVAILABLE"
            if not news_ok:
                return "NEWS_BLACKOUT"
        if len(self.guard.exec_errors) >= cfg.max_exec_errors_per_hour:
            return "TOO_MANY_EXEC_ERRORS"
        return None

    def _maybe_open(self, tick: Tick, acct: AccountState, signal: SignalSnapshot | None, now: float,
                    news_ok: bool | None) -> None:
        cfg, g = self.cfg, self.guard
        if self.entries_blocked_reason or signal is None or signal.bar_time == g.last_signal_bar:
            return
        g.last_signal_bar = signal.bar_time  # each closed bar is evaluated once
        if signal.direction is None:
            return
        why = self._entry_filters(tick, acct, now, news_ok)
        if why:
            self._event(now, "ENTRY_REJECTED", reason=why)
            return
        direction = signal.direction
        room = self._risk_room(acct)
        sizing = compute_base_lot(direction, acct.equity, signal.atr, tick.spread, cfg, self.broker.spec(),
                                  risk_budget_override=room)
        if not sizing.ok:
            self._event(now, "ENTRY_REJECTED", reason=sizing.reason, sizing=sizing.explain())
            return
        why = self._basket_margin_check(direction, sizing, tick, acct)
        if why:
            self._event(now, "ENTRY_REJECTED", reason=why)
            return
        if cfg.ai_mode is AIMode.FILTER:
            ctx = self._entry_context(direction, signal, tick, acct, sizing)
            dec = self.ai.entry_decision(ctx) if self.ai else None
            ok, why = self._validate_decision(dec, now, "ENTRY", direction)
            if not ok:
                self._event(now, "ENTRY_REJECTED", reason=f"AI_{why}")
                return
        self._seq += 1
        basket_id = f"{cfg.magic % 10000:04d}{int(now):x}"
        sides = leg_sides(direction, len(cfg.legs))
        legs = [LegRecord(i + 1, s, v) for i, (s, v) in enumerate(zip(sides, sizing.volumes))]
        self.basket = BasketState(
            basket_id=basket_id, direction=direction, phase=Phase.OPENING, legs=legs, start_time=now,
            start_equity=acct.equity, risk_money=sizing.risk_budget,
            target_money=cfg.target_pct / 100.0 * acct.equity,
            levels=Levels(0, 0, 0, 0, sizing.distance, sizing.stop_distance),
            sizing_note=sizing.explain(),
        )
        self.phase = Phase.OPENING
        self._max_float_loss = 0.0
        self._max_gross = 0.0
        self._event(now, "BASKET_OPEN", basket=basket_id, direction=direction.name, volumes=sizing.volumes,
                    distance=sizing.distance, risk=sizing.risk_budget, planned_max_loss=sizing.planned_max_loss)
        self._send_leg(now, legs[0], tick)

    def _basket_margin_check(self, direction: Side, sizing: SizingResult, tick: Tick, acct: AccountState) -> str | None:
        """Conservative: the margin of ALL planned legs is summed (no hedge reduction assumed)."""
        cfg = self.cfg
        total = 0.0
        for side, vol in zip(leg_sides(direction, len(sizing.volumes)), sizing.volumes):
            total += self.broker.calc_margin(side, vol, tick.ask if side is Side.BUY else tick.bid)
        projected = acct.margin + total
        if projected > 0 and acct.equity / projected * 100.0 < cfg.min_margin_level_pct:
            return "PROJECTED_MARGIN_LEVEL"
        if acct.equity - projected < cfg.min_free_margin_after_pct / 100.0 * acct.equity:
            return "INSUFFICIENT_FREE_MARGIN"
        return None

    # ------------------------------------------------------------------ AI glue
    def _validate_decision(self, dec: AIDecision | None, now: float, kind: str, direction: Side,
                           leg_index: int = 0) -> tuple[bool, str]:
        if dec is None:
            return False, "UNAVAILABLE"
        if dec.decision not in ("ALLOW", "BLOCK", "WAIT") or dec.direction not in ("BUY", "SELL", "NONE"):
            return False, "INVALID"
        if dec.kind != kind or (kind == "RECOVERY_LEG" and dec.leg_index != leg_index):
            return False, "MISMATCH"
        if now >= dec.expires_at_utc:
            return False, "EXPIRED"
        if now - dec.data_timestamp_utc > self.cfg.ai_max_data_age_sec:
            return False, "STALE"
        if dec.decision != "ALLOW":
            return False, dec.decision
        if dec.direction != direction.name:
            return False, "DIRECTION_MISMATCH"
        return True, "OK"

    def _account_ctx(self, acct: AccountState) -> dict[str, Any]:
        g = self.guard
        return {
            "equity": acct.equity, "balance": acct.balance, "margin": acct.margin,
            "margin_level": None if math.isinf(acct.margin_level) else acct.margin_level,
            "daily_pnl_pct": (acct.equity / g.sod_equity - 1) * 100 if g.sod_equity else 0.0,
            "drawdown_pct": (1 - acct.equity / g.peak_equity) * 100 if g.peak_equity else 0.0,
            "gross_lots": self._gross_exposure(), "net_lots": self._net_exposure(),
        }

    def _entry_context(self, direction: Side, signal: SignalSnapshot, tick: Tick, acct: AccountState,
                       sizing: SizingResult) -> dict[str, Any]:
        return {
            "kind": "ENTRY", "proposed_direction": direction.name, "time": tick.time,
            "data_timestamp_utc": signal.close_time, "bid": tick.bid, "ask": tick.ask,
            "atr": signal.atr, "distance": sizing.distance, "account": self._account_ctx(acct),
        }

    def _leg_context(self, leg: LegRecord, tick: Tick, acct: AccountState) -> dict[str, Any]:
        b = self.basket
        assert b is not None and b.levels is not None
        return {
            "kind": "RECOVERY_LEG", "leg_index": leg.index, "proposed_direction": leg.side.name,
            "time": tick.time, "bid": tick.bid, "ask": tick.ask, "upper": b.levels.upper,
            "lower": b.levels.lower, "account": self._account_ctx(acct),
        }

    # ------------------------------------------------------------------ persistence
    def to_json(self) -> str:
        def enc(o: Any) -> Any:
            if isinstance(o, Enum):
                return o.value
            raise TypeError(type(o))

        state = {
            "version": STATE_VERSION,
            "phase": self.phase.value,
            "basket": asdict(self.basket) if self.basket else None,
            "guard": asdict(self.guard),
            "max_float_loss": self._max_float_loss,
            "max_gross": self._max_gross,
        }
        return json.dumps(state, default=enc)

    def load_json(self, data: str) -> None:
        """Load persisted state. The engine still starts in RECONCILE and verifies against the broker."""
        s = json.loads(data)
        if s.get("version") != STATE_VERSION:
            raise ValueError("unsupported state version")
        g = s["guard"]
        g["kill"] = KillMode(g["kill"])
        self.guard = GuardState(**g)
        bd = s["basket"]
        if bd:
            bd["direction"] = Side(bd["direction"])
            bd["phase"] = Phase(bd["phase"])
            bd["legs"] = [LegRecord(**{**l, "side": Side(l["side"]), "state": LegState(l["state"])}) for l in bd["legs"]]
            bd["levels"] = Levels(**bd["levels"]) if bd["levels"] else None
            self.basket = BasketState(**bd)
        else:
            self.basket = None
        self._max_float_loss = s.get("max_float_loss", 0.0)
        self._max_gross = s.get("max_gross", 0.0)
        self.phase = Phase.RECONCILE
