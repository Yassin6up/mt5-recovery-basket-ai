"""Backtester, leakage, labels, walk-forward plumbing and stress scenarios."""

from dataclasses import replace

import numpy as np
import pytest

from lrb.core.config import StrategyConfig
from lrb.core.types import Tick
from lrb.research.backtest import run_backtest
from lrb.research.data import SynthParams, load_ticks_csv, synthetic_ticks, write_ticks_csv
from lrb.research.labels import LabelSet
from lrb.research.stress import default_scenarios


@pytest.fixture(scope="module")
def ticks():
    return list(synthetic_ticks(SynthParams(days=12, seed=3)))


def test_csv_roundtrip(tmp_path, ticks):
    p = tmp_path / "t.csv"
    write_ticks_csv(ticks[:1000], p)
    back = load_ticks_csv(p)
    assert len(back) == 1000 and back[0].bid == pytest.approx(ticks[0].bid)


def test_mt5_export_format(tmp_path):
    p = tmp_path / "mt5.csv"
    p.write_text("<DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>\n"
                 "2025.01.02\t10:00:00.123\t1.10000\t1.10010\t\t\t6\n"
                 "2025.01.02\t10:00:01.000\t\t1.10012\t\t\t4\n")
    t = load_ticks_csv(p, server_utc_offset_hours=2)
    assert len(t) == 2 and t[1].bid == 1.1 and t[1].ask == 1.10012
    assert t[0].time == pytest.approx(1735812000.123 - 7200)


def test_no_lookahead_future_changes_do_not_alter_past(ticks):
    cfg = StrategyConfig()
    cut = ticks[len(ticks) // 2].time
    altered = [t if t.time < cut else Tick(t.time, t.bid + 0.01, t.ask + 0.01) for t in ticks]
    a = run_backtest(ticks, cfg)
    b = run_backtest(altered, cfg)
    past_a = [(r.basket_id, round(r.realized_pnl, 6)) for r in a.reports if r.end_time < cut]
    past_b = [(r.basket_id, round(r.realized_pnl, 6)) for r in b.reports if r.end_time < cut]
    assert past_a == past_b


def test_backtest_accounting_consistency(ticks):
    res = run_backtest(ticks, StrategyConfig())
    m = res.metrics
    total = sum(r.realized_pnl for r in res.reports)
    assert m.final_equity - m.initial_equity == pytest.approx(total, abs=1e-6)
    assert all(r.legs_filled <= 3 for r in res.reports)
    assert m.max_gross_lots <= StrategyConfig().max_gross_lots + 1e-9


def test_label_selection_purges_crossing_horizons():
    L = LabelSet(np.zeros((4, 13)), np.array([0, 1, 0, 1.0]), np.zeros(4),
                 np.array([0.0, 10, 20, 30]), np.array([5.0, 25, 22, 40]))
    s = L.select(0, 24)
    assert list(s.t_start) == [0.0, 20.0]  # sample starting at 10 ends at 25 > 24 -> purged


def test_stress_scenarios_invariants():
    res = {r.name: r for r in default_scenarios()}
    for r in res.values():
        assert r.max_positions_seen <= 3, r.name
    assert res["gap_through_stop"].exceeded_planned_risk  # documented: stops are not guarantees
    assert res["failed_basket_close"].final_open_positions == 0
    assert res["failed_basket_close"].baskets == 1
    assert res["duplicate_signals"].baskets == 1 and res["duplicate_signals"].max_positions_seen == 1
    assert res["vps_restart_mid_basket"].baskets == 1
    assert res["insufficient_margin"].baskets == 0
    assert res["spread_spike_at_signal"].baskets == 0
    assert res["ai_outage_mid_basket"].baskets == 1  # existing basket closed; no new basket while AI down


def test_walkforward_plumbing_runs(ticks):
    from lrb.research.walkforward import walk_forward
    cfg = StrategyConfig(max_basket_duration_sec=6 * 3600)  # short horizon keeps labeling fast
    rep = walk_forward(ticks, cfg, n_folds=1, holdout_frac=0.3, label_spacing_sec=4 * 3600,
                       warmup_sec=2 * 86400, min_train=5)
    assert len(rep.folds) == 1 and rep.holdout is not None
    for seg in rep.folds + [rep.holdout]:
        assert seg.train[1] <= seg.validation[0] <= seg.validation[1] <= seg.test[0]
