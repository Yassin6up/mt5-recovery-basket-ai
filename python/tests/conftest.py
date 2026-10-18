from __future__ import annotations

import pytest

from lrb.core.config import StrategyConfig
from lrb.core.engine import Engine
from lrb.core.signal import SignalSnapshot
from lrb.core.types import Side, SymbolSpec, Tick
from lrb.research.simbroker import SimBroker

SPREAD = 0.00010
T0 = 1_760_000_000.0  # fixed epoch (a Thursday) for reproducibility


class Harness:
    """Drives an Engine over a scripted bid path with a forced baseline signal."""

    def __init__(self, cfg: StrategyConfig | None = None, broker: SimBroker | None = None, ai=None,
                 atr: float = 0.0020):
        self.cfg = cfg or StrategyConfig(cooldown_sec=0)
        self.broker = broker or SimBroker(SymbolSpec(), max_slippage_points=0.0, swap_long_points=0, swap_short_points=0)
        self.engine = Engine(self.cfg, self.broker, ai=ai)
        self.t = T0
        self.atr = atr
        self.bar = 0

    def tick(self, bid: float, spread: float = SPREAD, signal: Side | None = None, dt: float = 1.0, **kw) -> Tick:
        self.t += dt
        tk = Tick(self.t, round(bid, 6), round(bid + spread, 6))
        self.broker.set_tick(tk)
        sig = None
        if signal is not None:
            self.bar += 1
            sig = SignalSnapshot(self.bar, self.t, signal, self.atr, 0, 0, "TEST")
        self.engine.on_tick(tk, sig, **kw)
        return tk

    def path(self, bids, **kw) -> None:
        for b in bids:
            self.tick(b, **kw)

    def ramp(self, start: float, end: float, steps: int = 40, **kw) -> None:
        for i in range(1, steps + 1):
            self.tick(start + (end - start) * i / steps, **kw)


@pytest.fixture
def h() -> Harness:
    return Harness()
