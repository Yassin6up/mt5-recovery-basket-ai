import math

import pytest

from lrb.core.config import StrategyConfig
from lrb.core.sizing import (
    boundary_distance, compute_base_lot, floor_volume, leg_sides, levels_from_anchor, worst_case_scenarios,
)
from lrb.core.types import Side, SymbolSpec

SPEC = SymbolSpec()


def test_floor_volume_never_rounds_up():
    assert floor_volume(0.0679, SPEC) == 0.06
    assert floor_volume(0.0099, SPEC) == 0.0
    assert floor_volume(0.01, SPEC) == 0.01
    assert floor_volume(500, SPEC) == SPEC.volume_max


def test_leg_sides_alternate_and_mirror():
    assert leg_sides(Side.BUY, 3) == [Side.BUY, Side.SELL, Side.BUY]
    assert leg_sides(Side.SELL, 3) == [Side.SELL, Side.BUY, Side.SELL]


def test_levels_buy_and_sell_anchor():
    lv = levels_from_anchor(Side.BUY, 1.1000, 0.0020, 1.0)
    assert (lv.upper, lv.lower) == (1.1000, pytest.approx(1.0980))
    assert lv.stop_low == pytest.approx(1.0960) and lv.stop_high == pytest.approx(1.1020)
    lv = levels_from_anchor(Side.SELL, 1.1000, 0.0020, 1.0)
    assert (lv.lower, lv.upper) == (1.1000, pytest.approx(1.1020))


def test_boundary_distance_rules():
    cfg = StrategyConfig()
    d, why = boundary_distance(0.0005, 0.0001, cfg, SPEC)  # ATR below min distance
    assert why is None and d == pytest.approx(cfg.min_distance_points * SPEC.point)
    d, why = boundary_distance(0.0005, 0.0010, cfg, SPEC)  # spread rule dominates
    assert d == pytest.approx(cfg.min_distance_spread_mult * 0.0010)
    assert boundary_distance(0.05, 0.0001, cfg, SPEC)[1] == "DISTANCE_TOO_WIDE"
    assert boundary_distance(math.nan, 0.0001, cfg, SPEC)[1] == "ATR_UNAVAILABLE"


def test_worst_case_is_six_distances_for_default_progression():
    cfg = StrategyConfig(commission_per_lot_side=0, slippage_allowance_points=0, spread_stress_mult=1)
    scen = worst_case_scenarios(Side.BUY, 0.0020, 0.00001, cfg, SPEC)
    by_k = {s.legs_filled: s for s in scen}
    # k=1 long, stop 2D below entry; k=2 net short, stop D above upper => 3D; k=3 net long => 6D
    assert by_k[1].loss_price_units == pytest.approx(0.0040, abs=1e-5)
    assert by_k[2].loss_price_units == pytest.approx(0.0060, abs=3e-5)
    assert by_k[3].loss_price_units == pytest.approx(0.0120, abs=3e-5)


def test_sizing_respects_budget_and_mirrors():
    cfg = StrategyConfig()
    rb = compute_base_lot(Side.BUY, 10_000, 0.0020, 0.0001, cfg, SPEC)
    rs = compute_base_lot(Side.SELL, 10_000, 0.0020, 0.0001, cfg, SPEC)
    assert rb.ok and rs.ok
    assert rb.base_lot == rs.base_lot == 0.06
    assert rb.volumes == [0.06, 0.12, 0.18]
    assert rb.planned_max_loss <= rb.risk_budget * (1 - cfg.sizing_safety) + 1e-6
    assert "state k=3" in rb.explain()


def test_min_volume_above_budget_rejects_instead_of_rounding_up():
    cfg = StrategyConfig()
    r = compute_base_lot(Side.BUY, 300, 0.0020, 0.0001, cfg, SPEC)  # $3 budget
    assert not r.ok and r.reason == "MIN_VOLUME_EXCEEDS_RISK_BUDGET"


def test_exposure_caps_reduce_lot():
    cfg = StrategyConfig(max_gross_lots=0.12)
    r = compute_base_lot(Side.BUY, 100_000, 0.0020, 0.0001, cfg, SPEC)
    assert r.ok and r.base_lot == 0.02  # 0.12 gross / 6 multipliers


def test_custom_progression_is_used_verbatim():
    cfg = StrategyConfig(progression=(1, 1.5), max_positions=2)
    r = compute_base_lot(Side.BUY, 10_000, 0.0020, 0.0001, cfg, SPEC)
    assert r.ok and len(r.volumes) == 2
    assert r.volumes[1] == floor_volume(r.base_lot * 1.5, SPEC)


def test_risk_budget_override_shrinks_budget():
    cfg = StrategyConfig()
    r = compute_base_lot(Side.BUY, 10_000, 0.0020, 0.0001, cfg, SPEC, risk_budget_override=40)
    assert r.risk_budget == 40 and r.base_lot <= 0.02


def test_config_validation():
    with pytest.raises(ValueError):
        StrategyConfig(max_positions=4).validate()
    with pytest.raises(ValueError):
        StrategyConfig(allow_live=True).validate()  # demo_only still on
    with pytest.raises(ValueError):
        StrategyConfig(progression=(1, -2, 3)).validate()
    c = StrategyConfig.from_dict({"progression": [1, 2], "max_positions": 2, "ai_mode": "FILTER"})
    assert c.legs == (1.0, 2.0)
