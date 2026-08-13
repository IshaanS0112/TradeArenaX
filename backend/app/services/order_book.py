"""Limit order book with price-time priority matching.

Data structure
--------------
Two heaps of *price levels*, not of orders:

    bids: max-heap of int ticks (pushed negated), each level a FIFO deque
    asks: min-heap of int ticks,                  each level a FIFO deque

Price priority comes from the heap; time priority comes from the deque. Keeping
orders in per-level FIFOs rather than one big heap of (price, time) is what makes
time priority survive a partial fill: a resting order that is half-filled stays
at the head of its queue instead of being re-inserted behind orders that arrived
after it.

Three details that a naive implementation gets wrong, and this one does not:

1. **Prices are integers.** A level is identified by ``round(price / tick)``.
   Keying levels on floats produces phantom levels one ulp apart, because equal
   intentions do not produce equal floats (``100.10 - 0.05 + 0.05 != 100.10``).

2. **Time priority uses a sequence number, not a clock.** Every order takes the
   next value from a monotonic counter. Wall-clock timestamps tie - inside one
   simulation step they tie constantly - and a tie in the priority key means the
   matching order is whatever the container happened to do, which is not a
   specification.

3. **Cancellation is lazy.** Removing an order from the middle of a deque is
   O(n) in the level's depth. Cancelled orders are flagged and skipped when the
   matcher reaches them; the *displayed* depth stays correct because each level
   maintains its own live quantity total, which is decremented on cancel.

Empty levels are likewise popped from the heap lazily, when a best-price lookup
walks past them.
"""

from __future__ import annotations

import heapq
import itertools
from collections import deque
from dataclasses import dataclass, field

from app.enums import OrderStatus, OrderType, SelfTradePrevention, Side

# Sentinel: a MARKET order will cross any price, so it is represented as a limit
# at an unreachable one. Kept far outside any tick a GBM path could reach.
_UNBOUNDED = 1 << 60


class _Signal:
    """Control sentinel returned by the queue walk. Not an order."""

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.name}>"


_REAPED = _Signal("reaped")  # a dead order was dropped from the head
_CANCELLED_RESTING = _Signal("cancelled_resting")  # STP pulled a resting order
_CANCELLED_INCOMING = _Signal("cancelled_incoming")  # STP pulled the aggressor


@dataclass(slots=True)
class Order:
    """A single resting or in-flight order. Quantities are in shares."""

    order_id: int
    agent_id: str
    side: Side
    price_ticks: int
    quantity: float
    remaining: float
    sequence: int
    order_type: OrderType = OrderType.LIMIT
    step: int = 0
    status: OrderStatus = OrderStatus.OPEN
    #: Step at which an explicit cancel was applied. Persisted so a
    #: reconstruction does not show every quote a maker ever posted still
    #: resting, because the cancels that pulled them are not in the order stream.
    cancelled_at_step: int | None = None
    #: Position of that cancel in the book's single event sequence. The step
    #: alone is not enough to replay exactly: within one step a taker may hit a
    #: stale quote *before* the maker gets its turn to pull it, and a replay that
    #: applies all of a step's cancels up front deletes those executions.
    cancelled_at_sequence: int | None = None

    @property
    def is_live(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIAL) and self.remaining > 0

    @property
    def filled_quantity(self) -> float:
        return self.quantity - self.remaining


@dataclass(slots=True)
class Fill:
    """One execution. ``aggressor_side`` is the side of the incoming order."""

    price_ticks: int
    quantity: float
    buy_order_id: int
    sell_order_id: int
    buy_agent_id: str
    sell_agent_id: str
    aggressor_side: Side
    step: int

    @property
    def maker_order_id(self) -> int:
        return self.sell_order_id if self.aggressor_side is Side.BUY else self.buy_order_id

    @property
    def maker_agent_id(self) -> str:
        return self.sell_agent_id if self.aggressor_side is Side.BUY else self.buy_agent_id

    @property
    def taker_agent_id(self) -> str:
        return self.buy_agent_id if self.aggressor_side is Side.BUY else self.sell_agent_id


@dataclass(slots=True)
class _Level:
    """A single price level: FIFO queue plus a live quantity total.

    ``total`` excludes cancelled and filled orders still physically present in
    the deque, so depth queries do not have to walk the queue.
    """

    queue: deque[Order] = field(default_factory=deque)
    total: float = 0.0


class OrderBook:
    """A single-instrument limit order book.

    Not thread-safe by design: one book belongs to one simulation run, which is
    executed by one worker.
    """

    def __init__(
        self,
        tick_size: float,
        self_trade_prevention: SelfTradePrevention = SelfTradePrevention.CANCEL_RESTING,
    ) -> None:
        if tick_size <= 0:
            raise ValueError("tick_size must be positive")
        self.tick_size = tick_size
        self.stp = self_trade_prevention

        self._bid_levels: dict[int, _Level] = {}
        self._ask_levels: dict[int, _Level] = {}
        self._bid_heap: list[int] = []  # negated ticks -> max-heap
        self._ask_heap: list[int] = []
        self._orders: dict[int, Order] = {}

        self._order_ids = itertools.count(1)
        self._sequences = itertools.count(1)
        self.trades: list[Fill] = []

    # ------------------------------------------------------------------ ticks
    def to_ticks(self, price: float, side: Side | None = None) -> int:
        """Quantise a price to the tick grid.

        A limit price is rounded *away* from aggression - a buy limit down, a
        sell limit up - so quantisation can never make an order more aggressive
        than the agent asked for. With no side given (a reference/mid price)
        it rounds to nearest.
        """
        raw = price / self.tick_size
        if side is Side.BUY:
            return int(raw // 1)
        if side is Side.SELL:
            return -int((-raw) // 1)
        return int(round(raw))

    def to_price(self, ticks: int) -> float:
        return ticks * self.tick_size

    # ------------------------------------------------------------------- book
    def _levels(self, side: Side) -> dict[int, _Level]:
        return self._bid_levels if side is Side.BUY else self._ask_levels

    def _prune(self, side: Side) -> None:
        """Drop heap entries whose level is gone or empty of live quantity."""
        if side is Side.BUY:
            while self._bid_heap:
                level = self._bid_levels.get(-self._bid_heap[0])
                if level is not None and level.total > 0:
                    return
                self._bid_levels.pop(-heapq.heappop(self._bid_heap), None)
        else:
            while self._ask_heap:
                level = self._ask_levels.get(self._ask_heap[0])
                if level is not None and level.total > 0:
                    return
                self._ask_levels.pop(heapq.heappop(self._ask_heap), None)

    def best_bid_ticks(self) -> int | None:
        self._prune(Side.BUY)
        return -self._bid_heap[0] if self._bid_heap else None

    def best_ask_ticks(self) -> int | None:
        self._prune(Side.SELL)
        return self._ask_heap[0] if self._ask_heap else None

    @property
    def best_bid(self) -> float | None:
        t = self.best_bid_ticks()
        return None if t is None else self.to_price(t)

    @property
    def best_ask(self) -> float | None:
        t = self.best_ask_ticks()
        return None if t is None else self.to_price(t)

    @property
    def mid_price(self) -> float | None:
        """Mid of the touch. ``None`` when either side is empty.

        Deliberately not falling back to the last trade or to a reference price:
        a mid quoted from one side of the book is not a mid, and silently
        inventing one is how a backtest starts reporting profit that the market
        never offered. Callers decide what to do with the absence.
        """
        bid, ask = self.best_bid_ticks(), self.best_ask_ticks()
        if bid is None or ask is None:
            return None
        return self.to_price(bid + ask) / 2.0

    @property
    def spread(self) -> float | None:
        bid, ask = self.best_bid_ticks(), self.best_ask_ticks()
        if bid is None or ask is None:
            return None
        return self.to_price(ask - bid)

    def depth_at(self, side: Side, ticks: int) -> float:
        level = self._levels(side).get(ticks)
        return level.total if level else 0.0

    def snapshot(self, levels: int = 10) -> dict[str, object]:
        """Aggregated book, best price first. For display and for agent input."""

        def side_view(side: Side) -> list[dict[str, float]]:
            src = self._levels(side)
            ticks = sorted(
                (t for t, lv in src.items() if lv.total > 0),
                reverse=side is Side.BUY,
            )[:levels]
            out, cumulative = [], 0.0
            for t in ticks:
                qty = src[t].total
                cumulative += qty
                out.append(
                    {
                        "price": round(self.to_price(t), 10),
                        "quantity": qty,
                        "cumulative_quantity": cumulative,
                        "order_count": sum(1 for o in src[t].queue if o.is_live),
                    }
                )
            return out

        return {
            "bids": side_view(Side.BUY),
            "asks": side_view(Side.SELL),
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "mid_price": self.mid_price,
            "spread": self.spread,
            "total_trades": len(self.trades),
        }

    # ----------------------------------------------------------------- orders
    def submit(
        self,
        agent_id: str,
        side: Side,
        quantity: float,
        price: float | None = None,
        order_type: OrderType = OrderType.LIMIT,
        step: int = 0,
    ) -> tuple[Order, list[Fill]]:
        """Submit an order, match it, and rest any remainder if it is a limit.

        Returns the order (with its final status) and the fills it generated.
        """
        if quantity <= 0:
            raise ValueError("order quantity must be positive")
        if order_type is OrderType.LIMIT and price is None:
            raise ValueError("a LIMIT order requires a price")

        if order_type is OrderType.MARKET:
            price_ticks = _UNBOUNDED if side is Side.BUY else -_UNBOUNDED
        else:
            price_ticks = self.to_ticks(float(price), side)

        order = Order(
            order_id=next(self._order_ids),
            agent_id=agent_id,
            side=side,
            price_ticks=price_ticks,
            quantity=quantity,
            remaining=quantity,
            sequence=next(self._sequences),
            order_type=order_type,
            step=step,
        )
        self._orders[order.order_id] = order

        fills = self._match(order)

        if order.status is OrderStatus.CANCELLED:
            # Self-trade prevention pulled the aggressor. Its remaining quantity
            # is zero, but reporting it as FILLED would put a fictional
            # execution in the order log.
            pass
        elif order.remaining <= 0:
            order.status = OrderStatus.FILLED
        elif order.order_type is OrderType.MARKET:
            # Immediate-or-cancel: an unfillable remainder does not rest.
            order.status = OrderStatus.EXPIRED
        else:
            order.status = OrderStatus.PARTIAL if fills else OrderStatus.OPEN
            self._rest(order)

        return order, fills

    def _match(self, incoming: Order) -> list[Fill]:
        fills: list[Fill] = []
        opposite = incoming.side.opposite
        levels = self._levels(opposite)

        while incoming.remaining > 0:
            best = (
                self.best_ask_ticks() if incoming.side is Side.BUY else self.best_bid_ticks()
            )
            if best is None:
                break
            # Crossing test. A buy matches asks at or below its limit; a sell
            # matches bids at or above it.
            if incoming.side is Side.BUY:
                if best > incoming.price_ticks:
                    break
            elif best < incoming.price_ticks:
                break

            level = levels[best]
            progressed = self._match_at_level(incoming, level, fills)

            if level.total <= 0:
                self._prune(opposite)

            if not progressed:
                # Nothing at this level can be traded and nothing was removed,
                # so re-entering the loop would re-examine the identical state
                # forever. This happens under SKIP when the entire best level
                # belongs to the incoming order's own agent. Stopping rather
                # than walking to a worse price is deliberate: trading through
                # your own better-priced order to reach someone else's worse one
                # is a self-trade-through, and it is why most venues cancel
                # rather than skip.
                break

        return fills

    def _match_at_level(self, incoming: Order, level: _Level, fills: list[Fill]) -> bool:
        """Match ``incoming`` against one price level.

        Returns whether any progress was made - a trade, a cancel, or the
        reaping of a lazily-deleted order. The caller uses that to guarantee
        termination.
        """
        progressed = False

        while incoming.remaining > 0:
            resting = self._first_matchable(incoming, level)

            if resting is _CANCELLED_RESTING:
                progressed = True
                continue
            if resting is _CANCELLED_INCOMING:
                return True
            if resting is _REAPED:
                progressed = True
                continue
            if resting is None:
                return progressed

            traded = min(incoming.remaining, resting.remaining)
            # Price-time priority: execution happens at the *resting* order's
            # price. The passive side set the terms; the aggressor accepted them.
            # Using the incoming price instead would hand the aggressor a rebate
            # it never earned.
            incoming.remaining -= traded
            resting.remaining -= traded
            level.total -= traded

            if incoming.side is Side.BUY:
                buy_order, sell_order = incoming, resting
            else:
                buy_order, sell_order = resting, incoming

            fill = Fill(
                price_ticks=resting.price_ticks,
                quantity=traded,
                buy_order_id=buy_order.order_id,
                sell_order_id=sell_order.order_id,
                buy_agent_id=buy_order.agent_id,
                sell_agent_id=sell_order.agent_id,
                aggressor_side=incoming.side,
                step=incoming.step,
            )
            fills.append(fill)
            self.trades.append(fill)
            progressed = True

            if resting.remaining <= 0:
                resting.status = OrderStatus.FILLED
                if level.queue and level.queue[0] is resting:
                    level.queue.popleft()
            else:
                # Partially filled and still in place: time priority is
                # preserved, which is the whole point of the per-level FIFO.
                resting.status = OrderStatus.PARTIAL

        return progressed

    def _first_matchable(self, incoming: Order, level: _Level):
        """Walk the level's queue for the first order ``incoming`` may trade with.

        Returns the order, ``None`` if the level holds nothing tradable, or one
        of the sentinels below to tell the caller that state changed and it
        should re-examine the level.

        Non-live orders at the head are reaped here; interior ones are stepped
        over and reaped when they reach the head. That keeps the default path
        amortised O(1) per fill. Under SKIP the walk is O(depth), which is
        accepted because SKIP is not the default policy.
        """
        while level.queue and not level.queue[0].is_live:
            level.queue.popleft()
            return _REAPED

        for candidate in level.queue:
            if not candidate.is_live:
                continue

            if candidate.agent_id != incoming.agent_id:
                return candidate

            if self.stp is SelfTradePrevention.CANCEL_INCOMING:
                incoming.remaining = 0.0
                incoming.status = OrderStatus.CANCELLED
                return _CANCELLED_INCOMING

            if self.stp is SelfTradePrevention.CANCEL_RESTING:
                level.total -= candidate.remaining
                candidate.remaining = 0.0
                candidate.status = OrderStatus.CANCELLED
                return _CANCELLED_RESTING

            continue  # SKIP: keep looking further back in the queue

        return None

    def _rest(self, order: Order) -> None:
        levels = self._levels(order.side)
        level = levels.get(order.price_ticks)
        if level is None:
            level = _Level()
            levels[order.price_ticks] = level
            heapq.heappush(
                self._bid_heap if order.side is Side.BUY else self._ask_heap,
                -order.price_ticks if order.side is Side.BUY else order.price_ticks,
            )
        level.queue.append(order)
        level.total += order.remaining

    def cancel(self, order_id: int, step: int | None = None) -> bool:
        order = self._orders.get(order_id)
        if order is None or not order.is_live:
            return False
        level = self._levels(order.side).get(order.price_ticks)
        if level is not None:
            level.total -= order.remaining
        order.remaining = 0.0
        order.status = OrderStatus.CANCELLED
        order.cancelled_at_step = step
        # A cancel is an event in the same ordered stream as a submission, so it
        # takes the next sequence number. That is what makes the log replayable.
        order.cancelled_at_sequence = next(self._sequences)
        return True

    def cancel_all_for_agent(self, agent_id: str, step: int | None = None) -> int:
        """Pull every live order for one agent. Used for quote refresh.

        A market maker re-quotes every step; leaving the previous step's quotes
        resting would let it accumulate an unbounded stack of stale orders at
        prices the fair value has since walked away from.
        """
        return sum(
            1
            for o in list(self._orders.values())
            if o.agent_id == agent_id and o.is_live and self.cancel(o.order_id, step)
        )

    def open_orders(self, agent_id: str | None = None) -> list[Order]:
        return [
            o
            for o in self._orders.values()
            if o.is_live and (agent_id is None or o.agent_id == agent_id)
        ]

    def get_order(self, order_id: int) -> Order | None:
        return self._orders.get(order_id)

    @property
    def all_orders(self) -> list[Order]:
        return list(self._orders.values())

    # ------------------------------------------------------------ invariants
    def assert_invariants(self) -> None:
        """Check the properties the book must never violate.

        Called by the test suite after every mutation-heavy scenario, and once
        at the end of each simulation run. An order book that silently breaks
        one of these produces PnL numbers that look plausible and are wrong,
        which is the worst possible failure mode for this project.
        """
        bid, ask = self.best_bid_ticks(), self.best_ask_ticks()
        if bid is not None and ask is not None and bid >= ask:
            # A crossed book is normally a matching bug - executable liquidity
            # left resting. There is exactly one legitimate exception: under the
            # SKIP self-trade policy, an agent whose own order it may not match
            # rests on top of it, and the two sides of the cross belong to the
            # same agent. That is not executable by anyone, so it is allowed.
            crossing_agents = {
                o.agent_id
                for levels, cmp_ticks in (
                    (self._bid_levels, lambda t: t >= ask),
                    (self._ask_levels, lambda t: t <= bid),
                )
                for ticks, level in levels.items()
                if cmp_ticks(ticks)
                for o in level.queue
                if o.is_live
            }
            if len(crossing_agents) > 1:
                raise AssertionError(
                    f"book is crossed: best bid {bid} >= best ask {ask} across "
                    f"agents {sorted(crossing_agents)}. Matching left executable "
                    "liquidity resting."
                )
        for side, levels in ((Side.BUY, self._bid_levels), (Side.SELL, self._ask_levels)):
            for ticks, level in levels.items():
                live = sum(o.remaining for o in level.queue if o.is_live)
                if abs(live - level.total) > 1e-9:
                    raise AssertionError(
                        f"{side} level {ticks} cached total {level.total} != live {live}"
                    )
                for o in level.queue:
                    if o.is_live and o.price_ticks != ticks:
                        raise AssertionError(f"order {o.order_id} filed at wrong level")
        seqs = [o.sequence for o in self._orders.values()]
        if len(seqs) != len(set(seqs)):
            raise AssertionError("duplicate sequence numbers: time priority is ambiguous")
