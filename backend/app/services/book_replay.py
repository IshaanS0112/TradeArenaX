"""Reconstruct the order book at any historical step from persisted orders."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import OrderType, Side
from app.models import Order as OrderRow
from app.services.order_book import OrderBook


@dataclass
class ReplayResult:
    book: OrderBook
    step: int
    orders_replayed: int
    trades_replayed: int


def replay_book(
    db: Session,
    simulation_id: str,
    tick_size: float,
    up_to_step: int | None = None,
) -> ReplayResult:
    """Rebuild the book as of ``up_to_step`` (inclusive). ``None`` means the end."""
    stmt = select(OrderRow).where(OrderRow.simulation_id == simulation_id)
    if up_to_step is not None:
        stmt = stmt.where(OrderRow.step <= up_to_step)
    rows = list(db.scalars(stmt.order_by(OrderRow.sequence)).all())

    book = OrderBook(tick_size=tick_size)
    if not rows:
        return ReplayResult(
            book=book, step=up_to_step or 0, orders_replayed=0, trades_replayed=0
        )

    final_step = up_to_step if up_to_step is not None else max(r.step for r in rows)

    # One merged, sequence-ordered event stream.
    events: list[tuple[int, str, OrderRow]] = []
    for row in rows:
        events.append((row.sequence, "submit", row))
        if (
            row.cancelled_at_sequence is not None
            and row.cancelled_at_step is not None
            and row.cancelled_at_step <= final_step
        ):
            events.append((row.cancelled_at_sequence, "cancel", row))
    events.sort(key=lambda e: e[0])

    # The replayed book mints its own order ids, so cancels recorded against.
    id_map: dict[int, int] = {}

    for _, kind, row in events:
        if kind == "submit":
            order, _ = book.submit(
                agent_id=row.agent_id,
                side=Side(row.side),
                quantity=row.quantity,
                price=row.price,
                order_type=OrderType(row.order_type),
                step=row.step,
            )
            id_map[row.engine_order_id] = order.order_id
        else:
            replayed = id_map.get(row.engine_order_id)
            if replayed is not None:
                book.cancel(replayed, step=row.cancelled_at_step)

    return ReplayResult(
        book=book,
        step=final_step,
        orders_replayed=len(rows),
        trades_replayed=len(book.trades),
    )


@dataclass
class BookHistory:
    """A dense (step x relative price level) grid of resting quantity."""

    steps: list[int]
    mid: list[float | None]
    best_bid: list[float | None]
    best_ask: list[float | None]
    # : Resting quantity at the touch, which is what the microprice weights.
    bid_quantity: list[float]
    ask_quantity: list[float]
    grid: list[float]
    levels: int
    width: int
    tick_size: float
    stride: int


def replay_book_history(
    db: Session,
    simulation_id: str,
    tick_size: float,
    levels: int = 20,
    stride: int = 1,
    up_to_step: int | None = None,
) -> BookHistory:
    """Replay once, snapshotting the book every ``stride`` steps."""
    stmt = select(OrderRow).where(OrderRow.simulation_id == simulation_id)
    if up_to_step is not None:
        stmt = stmt.where(OrderRow.step <= up_to_step)
    rows = list(db.scalars(stmt.order_by(OrderRow.sequence)).all())

    width = 2 * levels + 1
    history = BookHistory(
        steps=[],
        mid=[],
        best_bid=[],
        best_ask=[],
        bid_quantity=[],
        ask_quantity=[],
        grid=[],
        levels=levels,
        width=width,
        tick_size=tick_size,
        stride=stride,
    )
    if not rows:
        return history

    final_step = up_to_step if up_to_step is not None else max(r.step for r in rows)

    events: list[tuple[int, str, OrderRow]] = []
    for row in rows:
        events.append((row.sequence, "submit", row))
        if (
            row.cancelled_at_sequence is not None
            and row.cancelled_at_step is not None
            and row.cancelled_at_step <= final_step
        ):
            events.append((row.cancelled_at_sequence, "cancel", row))
    events.sort(key=lambda e: e[0])

    book = OrderBook(tick_size=tick_size)
    id_map: dict[int, int] = {}
    last_mid_ticks: int | None = None

    def capture(step: int) -> None:
        nonlocal last_mid_ticks
        bid_t, ask_t = book.best_bid_ticks(), book.best_ask_ticks()
        if bid_t is not None and ask_t is not None:
            mid_ticks = (bid_t + ask_t) // 2
            mid_price: float | None = book.to_price(bid_t + ask_t) / 2.0
        else:
            # One-sided book: anchor the axis on the last two-sided mid.
            mid_ticks = last_mid_ticks if last_mid_ticks is not None else (
                bid_t if bid_t is not None else ask_t
            )
            mid_price = None
        if mid_ticks is None:
            return
        last_mid_ticks = mid_ticks

        row_values = [0.0] * width
        for j in range(width):
            offset = j - levels
            ticks = mid_ticks + offset
            bid_qty = book.depth_at(Side.BUY, ticks)
            if bid_qty:
                row_values[j] = bid_qty
                continue
            ask_qty = book.depth_at(Side.SELL, ticks)
            if ask_qty:
                row_values[j] = -ask_qty

        history.steps.append(step)
        history.mid.append(mid_price)
        history.best_bid.append(None if bid_t is None else book.to_price(bid_t))
        history.best_ask.append(None if ask_t is None else book.to_price(ask_t))
        history.bid_quantity.append(
            0.0 if bid_t is None else book.depth_at(Side.BUY, bid_t)
        )
        history.ask_quantity.append(
            0.0 if ask_t is None else book.depth_at(Side.SELL, ask_t)
        )
        history.grid.extend(row_values)

    next_capture = stride
    for _, kind, row in events:
        step = row.step if kind == "submit" else (row.cancelled_at_step or row.step)
        # Capture on the boundary: the book as it stood at the end.
        while step > next_capture:
            capture(next_capture)
            next_capture += stride

        if kind == "submit":
            order, _ = book.submit(
                agent_id=row.agent_id,
                side=Side(row.side),
                quantity=row.quantity,
                price=row.price,
                order_type=OrderType(row.order_type),
                step=row.step,
            )
            id_map[row.engine_order_id] = order.order_id
        else:
            replayed = id_map.get(row.engine_order_id)
            if replayed is not None:
                book.cancel(replayed, step=row.cancelled_at_step)

    while next_capture <= final_step:
        capture(next_capture)
        next_capture += stride

    return history
