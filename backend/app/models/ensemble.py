"""Ensemble runs and parameter sweeps."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base, JsonBlob, UUIDStr, new_uuid, utc_now


class Ensemble(Base):
    __tablename__ = "ensembles"

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    path_count: Mapped[int] = mapped_column(Integer, nullable=False)
    steps: Mapped[int] = mapped_column(Integer, nullable=False)
    # Config identical across every path - price process, shocks, agents.
    shared_config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    engine_config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    # QUEUED | RUNNING | COMPLETED | FAILED
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", nullable=False)
    completed_paths: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Percentiles, bootstrap CIs, fraction-positive, per agent.
    aggregates: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    # Per-path per-agent summary rows, kept so the distribution can be redrawn without re-running.
    paths: Mapped[list] = mapped_column(JsonBlob, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())


class Sweep(Base):
    __tablename__ = "sweeps"

    id: Mapped[str] = mapped_column(UUIDStr, primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # The grid that was searched: {agent_type, parameter: [values], ...}
    grid_spec: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    shared_config: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED", nullable=False)
    # Probability of backtest overfitting (combinatorially symmetric CV).
    pbo: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The cell with the highest in-sample Sharpe - an order statistic.
    best_cell: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    # The centroid of the top decile - the parameters you would deploy.
    robust_centroid: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    trial_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, server_default=func.now())


class SweepResult(Base):
    __tablename__ = "sweep_results"
    __table_args__ = (Index("ix_sweep_results_sweep", "sweep_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    sweep_id: Mapped[str] = mapped_column(
        UUIDStr, ForeignKey("sweeps.id", ondelete="CASCADE"), nullable=False
    )
    parameters: Mapped[dict] = mapped_column(JsonBlob, default=dict, nullable=False)
    in_sample_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_of_sample_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    deflated_sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    in_sample_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_of_sample_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    trial_count: Mapped[int] = mapped_column(Integer, nullable=False)
