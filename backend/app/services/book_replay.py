"""Reconstruct the order book at any historical step from persisted orders.

The in-memory book does not survive the request that created it, and pinning it
in a process-local cache would break the moment the API runs more than one
worker. So the book is rebuilt from the stored event log instead.

This is exact rather than approximate, and the reason it can be is that the
persisted stream is complete. Each order row carries:

- ``step`` - which simulation step it belonged to,
- ``sequence`` - its position in the book's single global event order,
- ``cancelled_at_sequence`` - the position of the cancel that pulled it, if any.

Replaying submissions and cancels **interleaved by sequence** through the same
matching engine reproduces the same book, because the matcher is deterministic
and depends on nothing else.

The sequence number is doing real work here, and getting this wrong is
instructive: an earlier version of this module applied all of a step's cancels
before any of that step's submissions. The resting book still came out identical,
but half the executions vanished - because within a step a taker often hits a
maker's stale quote *before* the maker's turn comes round to pull it, and
front-loading the cancels deletes exactly those trades. A reconstruction that
matches on shape while losing half the tape is the most dangerous kind of wrong.

Two properties this gives the project:

- The dashboard can scrub the book back through the volatility shock.
- Any reported PnL number can be traced to a book state, which is the difference
  between a result and a claim.
"""

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

    # One merged, sequence-ordered event stream. ``kind`` breaks ties defensively;
    # in practice a submission and a cancel never share a sequence number.
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

    # The replayed book mints its own order ids, so cancels recorded against the
    # original engine ids have to be translated.
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
