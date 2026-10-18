"""Shared decision function used by the FastAPI service AND the backtester provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from lrb.core.types import AIDecision, Bar

from .features import compute_features
from .model import ModelOutput


class Model(Protocol):
    version: str

    def predict(self, f) -> ModelOutput: ...


@dataclass(frozen=True)
class DecisionResult:
    decision: str
    direction: str
    model_version: str
    data_timestamp_utc: float
    expires_at_utc: float
    reason_codes: list[str]
    p_loss: float | None = None
    expected_value: float | None = None


def decide(
    *, kind: str, proposed_direction: str, bars: Sequence[Bar], period_sec: int, spread: float, distance: float,
    drawdown_pct: float, model: Model, now: float, ttl_sec: float, max_data_age_sec: float, leg_index: int = 0,
) -> DecisionResult:
    if proposed_direction not in ("BUY", "SELL"):
        return DecisionResult("BLOCK", "NONE", model.version, now, now + ttl_sec, ["INVALID_DIRECTION"])
    if kind == "RECOVERY_LEG":
        # No validated model exists for leg approval; the deterministic risk engine decides.
        # We only veto legs on clearly abnormal inputs (stale data).
        last_close = bars[-1].time + period_sec if bars else 0.0
        if not bars or now - last_close > max_data_age_sec:
            return DecisionResult("WAIT", "NONE", model.version, last_close, now + ttl_sec, ["STALE_INPUT"])
        return DecisionResult("ALLOW", proposed_direction, model.version, last_close, now + ttl_sec,
                              ["LEG_DEFERS_TO_RISK_ENGINE"])
    d = 1 if proposed_direction == "BUY" else -1
    f = compute_features(bars, d, period_sec, spread, distance, drawdown_pct)
    if f is None:
        return DecisionResult("WAIT", "NONE", model.version, now, now + ttl_sec, ["INSUFFICIENT_DATA"])
    if now - f.data_timestamp_utc > max_data_age_sec:
        return DecisionResult("WAIT", "NONE", model.version, f.data_timestamp_utc, now + ttl_sec, ["STALE_INPUT"])
    out = model.predict(f)
    direction = proposed_direction if out.decision == "ALLOW" else "NONE"
    return DecisionResult(out.decision, direction, model.version, f.data_timestamp_utc, now + ttl_sec,
                          out.reason_codes, out.p_loss, out.expected_value)


class ModelProvider:
    """DecisionProvider for the backtester (synchronous, optional simulated outage)."""

    def __init__(self, bars_view, model: Model, period_sec: int, ttl_sec: float = 60.0,
                 max_data_age_sec: float = 1200.0, outage=None):
        self.bars, self.model, self.period, self.ttl, self.max_age = bars_view, model, period_sec, ttl_sec, max_data_age_sec
        self.outage = outage  # callable(t) -> bool
        self.calls = 0

    def _run(self, ctx, kind: str) -> AIDecision | None:
        self.calls += 1
        now = ctx["time"]
        if self.outage and self.outage(now):
            return None
        r = decide(kind=kind, proposed_direction=ctx["proposed_direction"], bars=self.bars.closed,
                   period_sec=self.period, spread=ctx["ask"] - ctx["bid"], distance=ctx.get("distance", 0.0),
                   drawdown_pct=ctx["account"]["drawdown_pct"], model=self.model, now=now, ttl_sec=self.ttl,
                   max_data_age_sec=self.max_age, leg_index=ctx.get("leg_index", 0))
        return AIDecision(f"bt-{self.calls}", r.decision, r.direction, r.model_version, r.data_timestamp_utc,
                          r.expires_at_utc, tuple(r.reason_codes), kind, ctx.get("leg_index", 0))

    def entry_decision(self, ctx):
        return self._run(ctx, "ENTRY")

    def leg_decision(self, ctx):
        return self._run(ctx, "RECOVERY_LEG")
