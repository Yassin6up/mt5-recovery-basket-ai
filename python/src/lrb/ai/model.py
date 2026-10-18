"""Models returning ALLOW/BLOCK/WAIT.

* ``RulesModel`` (model_version ``rules-v1``): deterministic filter, returns NO probability.
* ``LogisticModel``: L2 logistic regression predicting the probability that a basket
  opened now ends with realized P&L <= 0 (outcome defined in labels.py; horizon = until
  the basket closes, bounded by max_basket_duration_sec). Platt-calibrated on a
  validation period that is chronologically after the training period. Probabilities
  are only reported for this evaluated model, together with its evaluation metrics.

Decision rule for LogisticModel: ALLOW iff
    (1 - p_loss) * avg_win - p_loss * |avg_loss| > ev_margin
using avg_win/avg_loss estimated on the training labels.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .features import FEATURE_NAMES, FeatureRow


@dataclass
class ModelOutput:
    decision: str
    reason_codes: list[str]
    p_loss: float | None = None
    expected_value: float | None = None


class RulesModel:
    version = "rules-v1"

    def __init__(self, max_spread_atr: float = 0.15, min_atr_rel: float = 0.6, max_atr_rel: float = 1.8,
                 max_rv_ratio: float = 2.0):
        self.max_spread_atr, self.min_atr_rel, self.max_atr_rel, self.max_rv_ratio = (
            max_spread_atr, min_atr_rel, max_atr_rel, max_rv_ratio)

    def predict(self, f: FeatureRow) -> ModelOutput:
        v = dict(zip(f.names, f.values))
        codes: list[str] = []
        block = False
        if v["spread_atr"] > self.max_spread_atr:
            codes.append("SPREAD_WIDE"); block = True
        else:
            codes.append("SPREAD_ACCEPTABLE")
        if not (self.min_atr_rel <= v["atr_rel"] <= self.max_atr_rel):
            codes.append("VOL_REGIME_OUTSIDE_BAND"); block = True
        if v["rv_ratio"] > self.max_rv_ratio:
            codes.append("VOL_SHOCK"); block = True
        if v["ema_gap_atr"] > 0 and v["ema_slope_atr"] > 0:
            codes.append("TREND_ALIGNED")
        else:
            codes.append("TREND_NOT_CONFIRMED"); block = True
        return ModelOutput("BLOCK" if block else "ALLOW", codes)


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1.0, iters: int = 500, lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Plain gradient descent on standardized X (deterministic, no external deps)."""
    n, k = X.shape
    w = np.zeros(k)
    b = 0.0
    for _ in range(iters):
        p = _sigmoid(X @ w + b)
        g = p - y
        w -= lr * (X.T @ g / n + l2 * w / n)
        b -= lr * g.mean()
    return w, b


def fit_platt(scores: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Fit p = sigmoid(A*score + B) by Newton iterations on log loss."""
    A, B = 1.0, 0.0
    for _ in range(100):
        p = _sigmoid(A * scores + B)
        g = np.array([((p - y) * scores).sum(), (p - y).sum()])
        wgt = p * (1 - p) + 1e-9
        H = np.array([[(wgt * scores * scores).sum(), (wgt * scores).sum()], [(wgt * scores).sum(), wgt.sum()]])
        H += np.eye(2) * 1e-6
        step = np.linalg.solve(H, g)
        A, B = A - step[0], B - step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(A), float(B)


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2)) if len(y) else math.nan


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p))) if len(y) else math.nan


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 5) -> list[dict[str, float]]:
    out = []
    edges = np.linspace(0, 1, bins + 1)
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.any():
            out.append({"bin": f"{edges[i]:.1f}-{edges[i+1]:.1f}", "n": int(m.sum()),
                        "mean_pred": float(p[m].mean()), "obs_rate": float(y[m].mean())})
    return out


@dataclass
class LogisticModel:
    version: str
    weights: list[float]
    bias: float
    mean: list[float]
    std: list[float]
    platt_a: float
    platt_b: float
    avg_win: float
    avg_loss: float
    ev_margin: float = 0.0
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))
    outcome: str = "realized basket P&L <= 0 (incl. commission, swap, slippage)"
    horizon: str = "until basket close, bounded by max_basket_duration_sec"
    meta: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def train(cls, X_tr: np.ndarray, y_tr: np.ndarray, pnl_tr: np.ndarray, X_va: np.ndarray, y_va: np.ndarray,
              version: str, l2: float = 5.0, ev_margin: float = 0.0, meta: dict[str, Any] | None = None) -> "LogisticModel":
        mu = X_tr.mean(axis=0)
        sd = X_tr.std(axis=0)
        sd[sd < 1e-12] = 1.0
        w, b = fit_logistic((X_tr - mu) / sd, y_tr, l2=l2)
        if len(y_va) >= 20 and 0 < y_va.mean() < 1:
            A, B = fit_platt(((X_va - mu) / sd) @ w + b, y_va)
        else:
            A, B = 1.0, 0.0
        wins = pnl_tr[pnl_tr > 0]
        losses = pnl_tr[pnl_tr <= 0]
        return cls(version, w.tolist(), float(b), mu.tolist(), sd.tolist(), A, B,
                   float(wins.mean()) if len(wins) else 0.0, float(losses.mean()) if len(losses) else 0.0,
                   ev_margin, meta=meta or {})

    def p_loss_batch(self, X: np.ndarray) -> np.ndarray:
        z = ((X - np.array(self.mean)) / np.array(self.std)) @ np.array(self.weights) + self.bias
        return _sigmoid(self.platt_a * z + self.platt_b)

    def predict(self, f: FeatureRow) -> ModelOutput:
        if tuple(f.names) != tuple(self.feature_names):
            return ModelOutput("WAIT", ["FEATURE_SCHEMA_MISMATCH"])
        p = float(self.p_loss_batch(f.values[None, :])[0])
        ev = (1 - p) * self.avg_win + p * self.avg_loss  # avg_loss is negative
        if ev > self.ev_margin:
            return ModelOutput("ALLOW", ["EV_POSITIVE"], p, ev)
        return ModelOutput("BLOCK", ["EV_NOT_POSITIVE"], p, ev)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: str | Path) -> "LogisticModel":
        d = json.loads(Path(path).read_text())
        m = cls(**d)
        if tuple(m.feature_names) != FEATURE_NAMES:
            raise ValueError("model feature schema does not match this code version")
        return m
