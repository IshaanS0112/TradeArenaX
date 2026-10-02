from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base, JsonBlob, UUIDStr, new_uuid, utc_now

if TYPE_CHECKING:  # pragma: no cover
    from app.models.simulation import Simulation


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    simulation_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("simulations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # The *resolved* config, defaults included - see Agent.resolve_config.
    config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    # Per-agent latency, in simulated microseconds: latency_in (market data.
    latency_config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    final_metrics: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    risk_flags: Mapped[list] = mapped_column(JsonBlob, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())

    simulation: Mapped["Simulation"] = relationship(back_populates="agents")
