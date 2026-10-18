import pytest

from lrb.core.accounting import basket_pnl, final_realized
from lrb.core.types import Deal, DealEntry, Position, Side, SymbolSpec

SPEC = SymbolSpec()


def _pos(ticket, side, vol, swap=0.0):
    return Position(ticket, "EURUSD", 1, side, vol, 1.1, 0, swap=swap)


def test_components_are_not_double_counted():
    positions = [_pos(1, Side.BUY, 0.1, swap=-1.5), _pos(2, Side.SELL, 0.2, swap=0.5)]
    profit = {1: -20.0, 2: 35.0}
    deals = [
        Deal(10, 1, "EURUSD", 1, DealEntry.IN, Side.BUY, 0.1, 1.1, 0, commission=-0.35),
        Deal(11, 2, "EURUSD", 1, DealEntry.IN, Side.SELL, 0.2, 1.1, 0, commission=-0.70),
        # position 3 already closed: its swap is realized on the OUT deal only
        Deal(12, 3, "EURUSD", 1, DealEntry.IN, Side.BUY, 0.1, 1.1, 0, commission=-0.35),
        Deal(13, 3, "EURUSD", 1, DealEntry.OUT, Side.SELL, 0.1, 1.1, 1, profit=12.0, swap=-0.4, commission=-0.35),
        Deal(13, 3, "EURUSD", 1, DealEntry.OUT, Side.SELL, 0.1, 1.1, 1, profit=12.0, swap=-0.4, commission=-0.35),  # duplicate row
        Deal(99, 77, "EURUSD", 1, DealEntry.OUT, Side.SELL, 1.0, 1.1, 1, profit=500.0),  # not ours
    ]
    p = basket_pnl(positions, lambda x: profit[x.ticket], deals, {1, 2, 3}, SPEC, 3.5, 0.0)
    assert p.floating == pytest.approx(15.0)
    assert p.open_swap == pytest.approx(-1.0)
    assert p.realized == pytest.approx(11.6)
    assert p.commissions == pytest.approx(-1.75)
    assert p.est_close_costs == pytest.approx(0.3 * 3.5)
    assert p.net == pytest.approx(15 - 1 + 11.6 - 1.75 - 1.05)


def test_slippage_allowance_in_close_estimate():
    p = basket_pnl([_pos(1, Side.BUY, 1.0)], lambda x: 0.0, [], {1}, SPEC, 0.0, 5.0)
    assert p.est_close_costs == pytest.approx(5 * SPEC.point * SPEC.value_per_price_unit_loss)


def test_final_realized_sums_all_basket_deals_once():
    deals = [
        Deal(1, 5, "EURUSD", 1, DealEntry.IN, Side.BUY, 0.1, 1.1, 0, commission=-0.35),
        Deal(2, 5, "EURUSD", 1, DealEntry.OUT, Side.SELL, 0.1, 1.1, 1, profit=10, swap=-1, commission=-0.35),
        Deal(2, 5, "EURUSD", 1, DealEntry.OUT, Side.SELL, 0.1, 1.1, 1, profit=10, swap=-1, commission=-0.35),
    ]
    assert final_realized(deals, {5}) == pytest.approx(10 - 1 - 0.7)
    assert final_realized(deals, {6}) == 0.0
