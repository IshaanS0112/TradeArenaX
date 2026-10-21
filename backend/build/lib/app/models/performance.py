from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base, UUIDStr, utc_now


class AgentPerformance(Base):
    """Per-step accounting snapshot for one agent.

    This is the table the Sharpe ratio and the drawdown are computed from, so it
    stores the mark price used for the unrealized leg as well. A PnL figure whose
    mark cannot be recovered is not auditable: mark-to-market on a one-sided book
    is a modelling choice, and the choice has to be visible in the row.
    """

    __tablename__ = "agent_performance"
    __table_args__ = (Index("ix_agent_performance_agent_step", "agent_id", "step"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    simulation_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    total_pnl: Mapped[float] = mapped_column(Float, nullable=False)
    mark_price: Mapped[float | None] = mapped_column(Float)
    inventory_risk_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())
