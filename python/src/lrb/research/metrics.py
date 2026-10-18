"""Backtest metrics. A high basket win rate alone is NOT evidence of profitability."""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Sequence

from lrb.core.engine import BasketReport


@dataclass
class Metrics:
    initial_equity: float
    final_equity: float
    net_return_pct: float
    max_equity_drawdown_pct: float
    max_basket_floating_loss: float
    baskets: int
    win_rate: float
    expectancy: float
    expectancy_ci95: tuple[float, float]
    avg_win: float
    avg_loss: float
    worst_basket: float
    best_basket: float
    profit_factor: float
    max_gross_lots: float
    max_margin_utilization_pct: float
    close_reasons: dict[str, int]
    legs_histogram: dict[int, int]
    stop_outs: int
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expectancy_ci95"] = list(self.expectancy_ci95)
        return d


def bootstrap_mean_ci(x: Sequence[float], n_boot: int = 2000, seed: int = 0) -> tuple[float, float]:
    if len(x) < 2:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    n = len(x)
    means = sorted(sum(x[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return (means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1])


def compute_metrics(
    reports: Sequence[BasketReport], initial_equity: float, final_equity: float, max_dd_pct: float,
    max_margin_util_pct: float, stop_outs: int = 0,
) -> Metrics:
    pnl = [r.realized_pnl for r in reports]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    n = len(pnl)
    return Metrics(
        initial_equity=initial_equity,
        final_equity=final_equity,
        net_return_pct=(final_equity / initial_equity - 1) * 100 if initial_equity else math.nan,
        max_equity_drawdown_pct=max_dd_pct,
        max_basket_floating_loss=min((r.max_floating_loss for r in reports), default=0.0),
        baskets=n,
        win_rate=len(wins) / n if n else math.nan,
        expectancy=sum(pnl) / n if n else math.nan,
        expectancy_ci95=bootstrap_mean_ci(pnl),
        avg_win=gross_win / len(wins) if wins else math.nan,
        avg_loss=-gross_loss / len(losses) if losses else math.nan,
        worst_basket=min(pnl, default=math.nan),
        best_basket=max(pnl, default=math.nan),
        profit_factor=gross_win / gross_loss if gross_loss > 0 else math.inf if gross_win > 0 else math.nan,
        max_gross_lots=max((r.max_gross_lots for r in reports), default=0.0),
        max_margin_utilization_pct=max_margin_util_pct,
        close_reasons=dict(Counter(r.close_reason for r in reports)),
        legs_histogram=dict(sorted(Counter(r.legs_filled for r in reports).items())),
        stop_outs=stop_outs,
    )


def format_metrics(m: Metrics, title: str) -> str:
    lo, hi = m.expectancy_ci95
    return "\n".join([
        f"== {title} ==",
        f"net return after costs : {m.net_return_pct:+.2f}%  (equity {m.initial_equity:.2f} -> {m.final_equity:.2f})",
        f"max equity drawdown    : {m.max_equity_drawdown_pct:.2f}%",
        f"worst basket float loss: {m.max_basket_floating_loss:.2f}",
        f"baskets                : {m.baskets}",
        f"win rate               : {m.win_rate:.1%}  (NOT evidence of profitability on its own)",
        f"expectancy / basket    : {m.expectancy:+.2f}  bootstrap 95% CI [{lo:+.2f}, {hi:+.2f}]",
        f"avg win / avg loss     : {m.avg_win:+.2f} / {m.avg_loss:+.2f}",
        f"best / worst basket    : {m.best_basket:+.2f} / {m.worst_basket:+.2f}",
        f"profit factor          : {m.profit_factor:.2f}",
        f"max gross lots         : {m.max_gross_lots:.2f}",
        f"max margin utilization : {m.max_margin_utilization_pct:.2f}% of equity",
        f"legs filled histogram  : {m.legs_histogram}",
        f"close reasons          : {m.close_reasons}",
        f"broker stop-outs       : {m.stop_outs}",
    ])
