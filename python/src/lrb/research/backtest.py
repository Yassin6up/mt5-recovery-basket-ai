"""Tick-by-tick backtest driving the same Engine the tests use.

Intrabar ordering is exact because every bid/ask tick is replayed in time order: legs are
never inferred from OHLC bars. Signals are computed only from bars that have CLOSED
before the tick being processed (no look-ahead).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from lrb.core.config import StrategyConfig
from lrb.core.engine import BasketReport, DecisionProvider, Engine, KillMode, Phase
from lrb.core.indicators import BarBuilder
from lrb.core.signal import SignalSnapshot, baseline_signal
from lrb.core.types import SymbolSpec, Tick

from .metrics import Metrics, compute_metrics
from .simbroker import Faults, SimBroker


@dataclass
class BrokerModel:
    balance: float = 10_000.0
    leverage: float = 100.0
    commission_per_lot_side: float = 3.5
    swap_long_points: float = -7.0
    swap_short_points: float = 2.0
    max_slippage_points: float = 2.0
    stop_out_level_pct: float = 50.0
    seed: int = 7

    def make(self, spec: SymbolSpec) -> SimBroker:
        return SimBroker(spec, balance=self.balance, leverage=self.leverage,
                         commission_per_lot_side=self.commission_per_lot_side,
                         swap_long_points=self.swap_long_points, swap_short_points=self.swap_short_points,
                         max_slippage_points=self.max_slippage_points, stop_out_level_pct=self.stop_out_level_pct,
                         seed=self.seed)


@dataclass
class BacktestResult:
    metrics: Metrics
    reports: list[BasketReport]
    equity_curve: list[tuple[float, float]]
    events: list[dict[str, Any]] = field(default_factory=list)
    signals: list[SignalSnapshot] = field(default_factory=list)


class BarsView:
    """Read-only access to closed bars for AI providers (no access to the forming bar)."""

    def __init__(self, builder: BarBuilder):
        self._b = builder

    @property
    def closed(self):
        return self._b.closed


def run_backtest(
    ticks: Iterable[Tick],
    cfg: StrategyConfig,
    spec: SymbolSpec | None = None,
    broker_model: BrokerModel | None = None,
    ai_factory: Callable[[BarsView], DecisionProvider] | None = None,
    faults: Faults | None = None,
    on_tick: Callable[[Engine, SimBroker, Tick], None] | None = None,
    equity_sample_sec: float = 300.0,
    keep_events: bool = False,
    trade_from: float | None = None,
) -> BacktestResult:
    """``trade_from``: ticks before this time only warm up indicators (no signals are acted on)."""
    spec = spec or SymbolSpec(name=cfg.symbol)
    bm = broker_model or BrokerModel(commission_per_lot_side=cfg.commission_per_lot_side)
    broker = bm.make(spec)
    if faults:
        broker.faults = faults
    bars = BarBuilder(cfg.signal_timeframe_sec)
    ai = ai_factory(BarsView(bars)) if ai_factory else None
    engine = Engine(cfg, broker, ai=ai)
    curve: list[tuple[float, float]] = []
    peak = bm.balance  # drawdown measured from the start of trading
    max_dd = 0.0
    max_util = 0.0
    last_sample = -1e18
    signals: list[SignalSnapshot] = []
    last: Tick | None = None
    for tk in ticks:
        broker.set_tick(tk)
        sig = None
        if bars.update(tk):
            sig = baseline_signal(bars.closed, cfg)
            if trade_from is not None and tk.time < trade_from:
                sig = None
            if sig is not None and sig.direction is not None:
                signals.append(sig)
        engine.on_tick(tk, sig)
        if on_tick:
            on_tick(engine, broker, tk)
        if engine.basket is not None or tk.time - last_sample >= equity_sample_sec:
            acct = broker.account()
            peak = max(peak, acct.equity)
            max_dd = max(max_dd, (1 - acct.equity / peak) * 100)
            if acct.equity > 0:
                max_util = max(max_util, acct.margin / acct.equity * 100)
            if tk.time - last_sample >= equity_sample_sec:
                curve.append((tk.time, acct.equity))
                last_sample = tk.time
        last = tk
    # End of data: flatten any open basket so its result is counted (reason END_OF_DATA).
    if last is not None and engine.basket is not None:
        engine.basket.close_reason = "END_OF_DATA"
        if engine.basket.phase is not Phase.CLOSING:
            engine._begin_close(last.time, "END_OF_DATA", None)
        for i in range(100):
            engine.on_timer(last.time + 2 * (i + 1))
            if engine.basket is None:
                break
    final_eq = broker.account().equity
    curve.append((last.time if last else 0.0, final_eq))
    m = compute_metrics(engine.reports, bm.balance, final_eq, max_dd, max_util, broker.stop_outs)
    return BacktestResult(m, list(engine.reports), curve, list(engine.events) if keep_events else [], signals)
