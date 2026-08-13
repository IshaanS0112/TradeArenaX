"""PnL accounting tests.

The cases that matter are the ones where the shortcut formula
(``sum(sells) - sum(buys)``) gives a different answer than FIFO lot matching:
open inventory, and position flips.
"""

from __future__ import annotations

import pytest

from app.enums import Side
from app.services.pnl import PositionTracker


def tracker(**kwargs) -> PositionTracker:
    return PositionTracker(agent_id="a", **kwargs)


def test_a_buy_alone_realizes_nothing():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)

    assert t.inventory == 10
    assert t.realized_pnl == 0.0, (
        "cash flow is -1000 here, but nothing has been realized: the shortcut "
        "formula would report a 1000 loss on a flat trade"
    )
    assert t.average_entry_price == pytest.approx(100.0)
    assert t.closed_round_trips == 0
    assert t.win_rate is None


def test_closed_round_trip_realizes_the_difference():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    t.apply_fill(Side.SELL, 10, 101.0, is_maker=True)

    assert t.inventory == 0
    assert t.realized_pnl == pytest.approx(10.0)
    assert t.unrealized_pnl(150.0) == 0.0, "a flat book cannot have unrealized PnL"
    assert t.closed_round_trips == 1
    assert t.win_rate == 1.0


def test_short_round_trip_realizes_the_opposite_sign():
    t = tracker()
    t.apply_fill(Side.SELL, 10, 100.0, is_maker=True)
    t.apply_fill(Side.BUY, 10, 99.0, is_maker=True)

    assert t.inventory == 0
    assert t.realized_pnl == pytest.approx(10.0), "sold high, bought back low"


def test_fifo_order_is_respected():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    t.apply_fill(Side.BUY, 10, 110.0, is_maker=True)
    t.apply_fill(Side.SELL, 10, 105.0, is_maker=True)

    # FIFO closes the 100 lot: +5 x 10 = +50. LIFO would close the 110 lot for -50.
    assert t.realized_pnl == pytest.approx(50.0)
    assert t.inventory == 10
    assert t.average_entry_price == pytest.approx(110.0)


def test_partial_close_leaves_the_rest_of_the_lot_open():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    t.apply_fill(Side.SELL, 4, 102.0, is_maker=True)

    assert t.realized_pnl == pytest.approx(8.0)
    assert t.inventory == 6
    assert t.average_entry_price == pytest.approx(100.0)
    assert t.open_lot_count() == 1


def test_position_flip_splits_the_fill():
    """The case a signed running total gets wrong.

    Long 10 at 100, then sell 25 at 105. That closes 10 long (+50) and opens 15
    short at 105. Booking the whole 25 as a close would report 125.
    """
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    t.apply_fill(Side.SELL, 25, 105.0, is_maker=True)

    assert t.realized_pnl == pytest.approx(50.0)
    assert t.inventory == -15
    assert t.average_entry_price == pytest.approx(105.0), (
        "the opened short leg is entered at the fill price, not blended with the "
        "closed long leg"
    )
    assert t.closed_round_trips == 1


def test_flip_back_and_forth_stays_consistent():
    t = tracker()
    t.apply_fill(Side.BUY, 5, 100.0, is_maker=True)
    t.apply_fill(Side.SELL, 15, 102.0, is_maker=True)  # close 5 (+10), open -10
    t.apply_fill(Side.BUY, 20, 101.0, is_maker=True)  # close -10 (+10), open +10

    assert t.realized_pnl == pytest.approx(20.0)
    assert t.inventory == 10
    assert t.average_entry_price == pytest.approx(101.0)
    assert t.closed_round_trips == 2


def test_unrealized_marks_open_inventory():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)

    assert t.unrealized_pnl(102.0) == pytest.approx(20.0)
    assert t.unrealized_pnl(98.0) == pytest.approx(-20.0)
    assert t.total_pnl(102.0) == pytest.approx(20.0)


def test_unrealized_on_a_short_has_the_right_sign():
    t = tracker()
    t.apply_fill(Side.SELL, 10, 100.0, is_maker=True)

    assert t.unrealized_pnl(98.0) == pytest.approx(20.0), "a short profits when price falls"
    assert t.unrealized_pnl(102.0) == pytest.approx(-20.0)


def test_missing_mark_does_not_invent_a_valuation():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    assert t.unrealized_pnl(None) == 0.0


def test_average_entry_is_quantity_weighted():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    t.apply_fill(Side.BUY, 30, 110.0, is_maker=True)

    assert t.average_entry_price == pytest.approx(107.5)


def test_win_rate_counts_round_trips_not_fills():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    t.apply_fill(Side.SELL, 5, 101.0, is_maker=True)  # win
    t.apply_fill(Side.SELL, 5, 99.0, is_maker=True)  # loss

    assert t.closed_round_trips == 2
    assert t.winning_round_trips == 1
    assert t.win_rate == pytest.approx(0.5)
    assert t.fill_count == 3


def test_fees_are_charged_by_role():
    t = tracker(maker_fee_bps=1.0, taker_fee_bps=5.0)
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=True)  # 1000 notional -> 0.10
    t.apply_fill(Side.SELL, 10, 100.0, is_maker=False)  # 1000 notional -> 0.50

    assert t.fees_paid == pytest.approx(0.60)
    assert t.realized_pnl == pytest.approx(-0.60), "flat trade, so PnL is just fees"


def test_zero_fees_by_default():
    t = tracker()
    t.apply_fill(Side.BUY, 10, 100.0, is_maker=False)
    assert t.fees_paid == 0.0


def test_inventory_risk_score():
    t = tracker()
    t.apply_fill(Side.BUY, 150, 100.0, is_maker=True)

    assert t.inventory_risk_score(200) == pytest.approx(0.75)
    assert t.inventory_risk_score(100) == pytest.approx(1.5)
    with pytest.raises(ValueError):
        t.inventory_risk_score(0)


def test_risk_score_is_symmetric_in_direction():
    long_t, short_t = tracker(), tracker()
    long_t.apply_fill(Side.BUY, 50, 100.0, is_maker=True)
    short_t.apply_fill(Side.SELL, 50, 100.0, is_maker=True)

    assert long_t.inventory_risk_score(100) == short_t.inventory_risk_score(100)


def test_rejects_non_positive_quantity():
    t = tracker()
    with pytest.raises(ValueError):
        t.apply_fill(Side.BUY, 0, 100.0, is_maker=True)


def test_inventory_snaps_to_zero_through_float_dust():
    """Repeated fractional fills must not leave a 1e-17 position behind.

    A residual inventory of 1e-17 is harmless in itself and poisonous in effect:
    ``average_entry_price`` stays non-None, unrealized PnL keeps being computed
    against it, and the agent never reads as flat.
    """
    t = tracker()
    for _ in range(3):
        t.apply_fill(Side.BUY, 0.1, 100.0, is_maker=True)
    for _ in range(3):
        t.apply_fill(Side.SELL, 0.1, 100.0, is_maker=True)

    assert t.inventory == 0.0
    assert t.unrealized_pnl(200.0) == 0.0


def test_two_agents_of_one_trade_sum_to_zero():
    """The zero-sum identity, at the level of a single fill."""
    buyer, seller = tracker(), tracker()
    buyer.apply_fill(Side.BUY, 10, 100.0, is_maker=True)
    seller.apply_fill(Side.SELL, 10, 100.0, is_maker=False)
    buyer.apply_fill(Side.SELL, 10, 103.0, is_maker=True)
    seller.apply_fill(Side.BUY, 10, 103.0, is_maker=False)

    assert buyer.realized_pnl + seller.realized_pnl == pytest.approx(0.0)
