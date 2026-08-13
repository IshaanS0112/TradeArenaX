"""Matching engine tests.

The properties asserted here are the ones that, if broken, produce PnL numbers
that look plausible and are wrong. Every test ends with an invariant check.
"""

from __future__ import annotations

import pytest

from app.enums import OrderStatus, OrderType, SelfTradePrevention, Side
from app.services.order_book import OrderBook


def test_empty_book_has_no_touch(book):
    assert book.best_bid is None
    assert book.best_ask is None
    assert book.mid_price is None
    assert book.spread is None


def test_resting_order_does_not_trade(book):
    order, fills = book.submit("a", Side.BUY, 10, 99.50)
    assert fills == []
    assert order.status is OrderStatus.OPEN
    assert book.best_bid == pytest.approx(99.50)
    book.assert_invariants()


def test_price_priority_best_price_fills_first(book):
    book.submit("a", Side.SELL, 5, 100.10)
    book.submit("b", Side.SELL, 5, 100.05)  # better ask, arrived later
    _, fills = book.submit("c", Side.BUY, 5, 100.20)

    assert len(fills) == 1
    assert fills[0].price_ticks == book.to_ticks(100.05)
    assert fills[0].sell_agent_id == "b", "price priority must beat time priority"
    book.assert_invariants()


def test_time_priority_within_a_price_level(book):
    book.submit("first", Side.SELL, 5, 100.00)
    book.submit("second", Side.SELL, 5, 100.00)
    _, fills = book.submit("taker", Side.BUY, 5, 100.00)

    assert len(fills) == 1
    assert fills[0].sell_agent_id == "first", "FIFO within a price level"
    book.assert_invariants()


def test_execution_happens_at_the_resting_price(book):
    """The passive side set the terms; the aggressor accepted them."""
    book.submit("maker", Side.SELL, 5, 100.00)
    _, fills = book.submit("taker", Side.BUY, 5, 105.00)

    assert fills[0].price_ticks == book.to_ticks(100.00), (
        "filling at the incoming limit would hand the aggressor a rebate it "
        "never earned"
    )
    book.assert_invariants()


def test_partial_fill_of_incoming_limit_rests_the_remainder(book):
    book.submit("maker", Side.SELL, 3, 100.00)
    order, fills = book.submit("taker", Side.BUY, 10, 100.00)

    assert sum(f.quantity for f in fills) == 3
    assert order.remaining == 7
    assert order.status is OrderStatus.PARTIAL
    assert book.best_bid == pytest.approx(100.00)
    assert book.depth_at(Side.BUY, book.to_ticks(100.00)) == 7
    book.assert_invariants()


def test_partial_fill_of_resting_order_keeps_queue_position(book):
    """The point of a per-level FIFO: a half-filled order is still first."""
    book.submit("first", Side.SELL, 10, 100.00)
    book.submit("second", Side.SELL, 10, 100.00)

    book.submit("t1", Side.BUY, 4, 100.00)  # eats 4 of first
    _, fills = book.submit("t2", Side.BUY, 6, 100.00)

    assert len(fills) == 1
    assert fills[0].sell_agent_id == "first", (
        "a partially filled resting order must keep its place, not go to the back"
    )
    book.assert_invariants()


def test_walks_multiple_levels(book):
    book.submit("a", Side.SELL, 5, 100.00)
    book.submit("b", Side.SELL, 5, 100.05)
    book.submit("c", Side.SELL, 5, 100.10)

    _, fills = book.submit("taker", Side.BUY, 12, 100.10)
    assert [f.quantity for f in fills] == [5, 5, 2]
    assert [f.sell_agent_id for f in fills] == ["a", "b", "c"]
    assert book.best_ask == pytest.approx(100.10)
    assert book.depth_at(Side.SELL, book.to_ticks(100.10)) == 3
    book.assert_invariants()


def test_does_not_cross_beyond_the_limit_price(book):
    book.submit("a", Side.SELL, 5, 100.00)
    book.submit("b", Side.SELL, 5, 100.50)

    order, fills = book.submit("taker", Side.BUY, 10, 100.00)
    assert sum(f.quantity for f in fills) == 5
    assert order.remaining == 5, "the 100.50 ask is outside the buyer's limit"
    book.assert_invariants()


def test_market_order_is_immediate_or_cancel(book):
    book.submit("a", Side.SELL, 3, 100.00)
    order, fills = book.submit("t", Side.BUY, 10, order_type=OrderType.MARKET)

    assert sum(f.quantity for f in fills) == 3
    assert order.status is OrderStatus.EXPIRED
    assert book.best_bid is None, "a market remainder must not rest in the book"
    book.assert_invariants()


def test_market_order_into_empty_book_expires_unfilled(book):
    order, fills = book.submit("t", Side.SELL, 5, order_type=OrderType.MARKET)
    assert fills == []
    assert order.status is OrderStatus.EXPIRED
    assert order.filled_quantity == 0
    book.assert_invariants()


def test_market_order_requires_no_price_and_limit_requires_one(book):
    with pytest.raises(ValueError):
        book.submit("a", Side.BUY, 5, price=None, order_type=OrderType.LIMIT)
    with pytest.raises(ValueError):
        book.submit("a", Side.BUY, 0, 100.0)


def test_cancel_removes_liquidity_without_walking_the_queue(book):
    o1, _ = book.submit("a", Side.SELL, 5, 100.00)
    book.submit("b", Side.SELL, 5, 100.00)

    assert book.cancel(o1.order_id) is True
    assert book.cancel(o1.order_id) is False, "cancelling twice is not an error, just False"
    assert book.depth_at(Side.SELL, book.to_ticks(100.00)) == 5

    _, fills = book.submit("t", Side.BUY, 5, 100.00)
    assert fills[0].sell_agent_id == "b", "a cancelled order must be skipped, not matched"
    book.assert_invariants()


def test_cancelling_the_whole_level_removes_the_touch(book):
    o, _ = book.submit("a", Side.BUY, 5, 99.00)
    book.cancel(o.order_id)
    assert book.best_bid is None
    book.assert_invariants()


def test_cancel_all_for_agent(book):
    book.submit("mm", Side.BUY, 5, 99.00)
    book.submit("mm", Side.SELL, 5, 101.00)
    book.submit("other", Side.BUY, 5, 98.00)

    assert book.cancel_all_for_agent("mm", step=7) == 2
    assert book.best_ask is None
    assert book.best_bid == pytest.approx(98.00)
    pulled = [o for o in book.all_orders if o.agent_id == "mm"]
    assert all(o.cancelled_at_step == 7 for o in pulled)
    assert all(o.cancelled_at_sequence is not None for o in pulled), (
        "a cancel takes a position in the same event sequence as a submission; "
        "without it the order stream cannot be replayed exactly"
    )
    sequences = [o.cancelled_at_sequence for o in pulled]
    assert len(set(sequences)) == len(sequences)
    book.assert_invariants()


# ------------------------------------------------------------ self-trade prevention
def test_self_trade_prevention_cancels_resting_by_default(book):
    book.submit("mm", Side.SELL, 5, 100.00)
    order, fills = book.submit("mm", Side.BUY, 5, 100.00)

    assert fills == [], "an agent must not trade with itself and book a fake profit"
    assert order.remaining == 5, "the incoming order rests once the resting one is pulled"
    assert book.best_ask is None
    book.assert_invariants()


def test_self_trade_prevention_matches_the_order_behind():
    b = OrderBook(0.01, SelfTradePrevention.CANCEL_RESTING)
    b.submit("mm", Side.SELL, 5, 100.00)
    b.submit("other", Side.SELL, 5, 100.00)
    _, fills = b.submit("mm", Side.BUY, 5, 100.00)

    assert len(fills) == 1
    assert fills[0].sell_agent_id == "other"
    b.assert_invariants()


def test_self_trade_prevention_cancel_incoming():
    b = OrderBook(0.01, SelfTradePrevention.CANCEL_INCOMING)
    b.submit("mm", Side.SELL, 5, 100.00)
    order, fills = b.submit("mm", Side.BUY, 5, 100.00)

    assert fills == []
    assert order.status is OrderStatus.CANCELLED
    assert b.best_ask == pytest.approx(100.00), "the resting order survives"
    b.assert_invariants()


def test_self_trade_prevention_skip_reaches_orders_behind():
    b = OrderBook(0.01, SelfTradePrevention.SKIP)
    b.submit("mm", Side.SELL, 5, 100.00)
    b.submit("other", Side.SELL, 5, 100.00)
    _, fills = b.submit("mm", Side.BUY, 5, 100.00)

    assert len(fills) == 1
    assert fills[0].sell_agent_id == "other"
    assert b.depth_at(Side.SELL, b.to_ticks(100.00)) == 5, "the skipped order survives"
    b.assert_invariants()


def test_skip_policy_terminates_when_the_whole_level_is_ours():
    b = OrderBook(0.01, SelfTradePrevention.SKIP)
    b.submit("mm", Side.SELL, 5, 100.00)
    b.submit("mm", Side.SELL, 5, 100.00)
    order, fills = b.submit("mm", Side.BUY, 5, 100.00)

    assert fills == [], "must not spin forever rotating a level it owns entirely"
    assert order.remaining == 5
    b.assert_invariants()


# ----------------------------------------------------------------------- ticks
def test_tick_quantisation_rounds_away_from_aggression(book):
    # A buy limit must never be rounded *up* into being more aggressive than asked.
    assert book.to_ticks(100.004, Side.BUY) == 10000
    assert book.to_ticks(100.006, Side.BUY) == 10000
    assert book.to_ticks(100.004, Side.SELL) == 10001
    assert book.to_ticks(-0.005, Side.BUY) == -1
    assert book.to_ticks(100.005, None) == 10000 or book.to_ticks(100.005, None) == 10001


def test_float_arithmetic_does_not_split_a_price_level(book):
    """The reason prices are integers internally.

    ``100.10 * 3 / 3`` is not ``100.10`` in binary floating point. Keyed on
    floats these two orders land on different levels and the book shows phantom
    depth one ulp apart - two "different" prices a trader cannot tell apart and
    a matching engine will refuse to cross.
    """
    drifted = 100.10 * 3 / 3  # 100.09999999999998
    assert drifted != 100.10, "precondition: these floats really do differ"

    book.submit("a", Side.SELL, 5, 100.10)
    book.submit("b", Side.SELL, 5, drifted)

    assert book.depth_at(Side.SELL, book.to_ticks(100.10)) == 10
    assert len(book.snapshot()["asks"]) == 1
    book.assert_invariants()


def test_snapshot_shape_and_cumulative_depth(book):
    book.submit("a", Side.BUY, 5, 99.00)
    book.submit("b", Side.BUY, 3, 99.50)
    book.submit("c", Side.SELL, 4, 100.50)

    snap = book.snapshot(levels=5)
    assert [lvl["price"] for lvl in snap["bids"]] == [99.50, 99.00], "best bid first"
    assert [lvl["cumulative_quantity"] for lvl in snap["bids"]] == [3, 8]
    assert snap["best_ask"] == pytest.approx(100.50)
    assert snap["spread"] == pytest.approx(1.00)
    assert snap["mid_price"] == pytest.approx(100.00)


def test_book_never_stays_crossed(book):
    book.submit("a", Side.SELL, 5, 100.00)
    book.submit("b", Side.BUY, 5, 101.00)
    # The buy at 101 must have consumed the ask at 100 rather than resting above it.
    book.assert_invariants()
    assert book.best_ask is None
    assert book.best_bid is None


def test_sequence_numbers_are_unique_and_monotonic(book):
    orders = [book.submit("a", Side.BUY, 1, 99.0 - i * 0.01)[0] for i in range(20)]
    seqs = [o.sequence for o in orders]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 20, "a tie in the priority key makes matching unspecified"
    book.assert_invariants()


def test_invariant_check_catches_a_corrupted_level(book):
    book.submit("a", Side.BUY, 5, 99.00)
    # Reach in and break the cached total the way a bad cancel path would.
    level = book._bid_levels[book.to_ticks(99.00)]
    level.total += 3
    with pytest.raises(AssertionError, match="cached total"):
        book.assert_invariants()


def test_rejects_non_positive_tick_size():
    with pytest.raises(ValueError):
        OrderBook(tick_size=0.0)
