from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base, UUIDStr, utc_now


class Trade(Base):
    """One execution."""

    __tablename__ = "trades"
    __table_args__ = (Index("ix_trades_sim_step", "simulation_id", "step"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sell_order_id: Mapped[int] = mapped_column(Integer, nullable=False)
    buy_agent_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    sell_agent_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    aggressor_side: Mapped[str] = mapped_column(String(4), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    # Trade-level adverse-selection measures.
    mid_at_trade: Mapped[float | None] = mapped_column(Float, nullable=True)
    effective_half_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    realised_half_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_impact: Mapped[float | None] = mapped_column(Float, nullable=True)
    horizon_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())
