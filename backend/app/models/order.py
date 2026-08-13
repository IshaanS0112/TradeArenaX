from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base, UUIDStr, utc_now


class Order(Base):
    """One submitted order.

    ``sequence`` is the engine's monotonic priority counter, persisted because
    it - not ``created_at`` - is what determined this order's place in the
    queue. Reconstructing a book from timestamps alone gives you a different
    book than the one that actually matched, since many orders share a step.
    """

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_sim_step", "simulation_id", "step"),
        Index("ix_orders_agent", "agent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    engine_order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False)
    price: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Set when the order was explicitly pulled (quote refresh). Together with
    # ``sequence`` and ``cancelled_at_sequence`` this makes the order stream a
    # complete event log, which is what lets the book be replayed exactly - see
    # services/book_replay.py for why the step alone is not sufficient.
    cancelled_at_step: Mapped[int | None] = mapped_column(Integer)
    cancelled_at_sequence: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())
