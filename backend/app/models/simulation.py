from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base, JsonBlob, UUIDStr, new_uuid, utc_now

if TYPE_CHECKING:  # pragma: no cover
    # SQLAlchemy resolves the related class through its own registry by name, so
    # no runtime import is needed here - and importing it at runtime would make
    # simulation.py and agent.py circular.
    from app.models.agent import Agent


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    duration_steps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The resolved parameter set, not the request body: defaults are filled in
    # before it is stored, so a run can be reproduced from the row alone.
    price_process_config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    volatility_shock_config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    # Engine-level constants in force for this run (tick size, fees, capital
    # base, steps per year). Without these the stored Sharpe cannot be checked.
    engine_config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    run_summary: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="CREATED", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())

    agents: Mapped[list["Agent"]] = relationship(
        back_populates="simulation", cascade="all, delete-orphan", lazy="selectin"
    )
    steps: Mapped[list["SimulationStep"]] = relationship(
        back_populates="simulation", cascade="all, delete-orphan"
    )


class SimulationStep(Base):
    """Per-step market state. The series behind every chart in the dashboard.

    Not in the original schema sketch, and added deliberately: without it the
    order-book chart and the "what happened at the shock" narrative have to be
    reconstructed from the trade log, which cannot show a step where the book
    moved but nothing traded - which is most steps.
    """

    __tablename__ = "simulation_steps"
    __table_args__ = (Index("ix_simulation_steps_sim_step", "simulation_id", "step"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    simulation_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_price: Mapped[float] = mapped_column(Float, nullable=False)
    # mid_price is null whenever a side of the book was empty; mark_price is the
    # value actually used to mark inventory that step and is never null.
    mid_price: Mapped[float | None] = mapped_column(Float)
    mark_price: Mapped[float | None] = mapped_column(Float)
    best_bid: Mapped[float | None] = mapped_column(Float)
    best_ask: Mapped[float | None] = mapped_column(Float)
    spread: Mapped[float | None] = mapped_column(Float)
    volatility: Mapped[float] = mapped_column(Float, nullable=False)
    trade_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shock_fired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    simulation: Mapped["Simulation"] = relationship(back_populates="steps")
