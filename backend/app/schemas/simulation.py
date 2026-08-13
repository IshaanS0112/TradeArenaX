"""Request and response contracts.

Validation here is not decoration. A negative volatility, a shock scheduled past
the end of the run, or a step count of a million are all requests the engine
would either reject deep inside a loop or accept and spend ten minutes on. They
are cheaper and clearer to reject at the boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import AgentType


class VolatilityShockConfig(BaseModel):
    step: int = Field(..., ge=1, description="Step at which the jump is applied")
    magnitude_pct: float = Field(
        ..., gt=-100.0, description="Instantaneous price jump, in percent"
    )
    vol_multiplier: float = Field(
        1.0, ge=1.0, description="Multiplier applied to sigma from this step, decaying back"
    )
    vol_half_life_steps: int = Field(50, ge=1)


class PriceProcessConfig(BaseModel):
    initial_price: float = Field(100.0, gt=0)
    drift: float = Field(0.0, description="Annualised mu")
    volatility: float = Field(0.30, ge=0, le=10.0, description="Annualised sigma")
    random_seed: int = Field(42, description="Same seed + same config = same path")


class SimulationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    price_process: PriceProcessConfig = PriceProcessConfig()
    shocks: list[VolatilityShockConfig] = Field(default_factory=list, max_length=20)

    @field_validator("shocks")
    @classmethod
    def _unique_steps(cls, shocks: list[VolatilityShockConfig]):
        steps = [s.step for s in shocks]
        if len(steps) != len(set(steps)):
            raise ValueError("two shocks cannot be scheduled at the same step")
        return shocks


class AgentCreate(BaseModel):
    name: str | None = Field(None, max_length=120)
    agent_type: AgentType
    config: dict[str, Any] = Field(default_factory=dict)


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    simulation_id: str
    name: str
    agent_type: str
    config: dict[str, Any]
    final_metrics: dict[str, Any]
    risk_flags: list[Any]
    created_at: datetime


class RunRequest(BaseModel):
    steps: int = Field(..., ge=1, le=20_000)
    # Every step of every agent is a row. A 5,000-step run with 3 agents is
    # 15,000 performance rows, which is fine to store and pointless to chart.
    # Downsampling on write keeps the table useful without losing the shape.
    persist_every_n_steps: int = Field(1, ge=1, le=1000)


class RunSummary(BaseModel):
    simulation_id: str
    steps_run: int
    total_trades: int
    total_orders: int
    configured_volatility: float
    realized_volatility: float | None
    steps_with_no_two_sided_book: int
    final_reference_price: float
    final_mid_price: float | None
    warnings: list[str]
    agent_metrics: dict[str, dict[str, Any]]


class SimulationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    duration_steps: int
    status: str
    price_process_config: dict[str, Any]
    volatility_shock_config: dict[str, Any]
    engine_config: dict[str, Any]
    run_summary: dict[str, Any]
    created_at: datetime
    agents: list[AgentRead] = Field(default_factory=list)


class BookLevel(BaseModel):
    price: float
    quantity: float
    cumulative_quantity: float
    order_count: int


class OrderBookSnapshot(BaseModel):
    bids: list[BookLevel]
    asks: list[BookLevel]
    best_bid: float | None
    best_ask: float | None
    mid_price: float | None
    spread: float | None
    total_trades: int
    # Present when the snapshot is rebuilt from stored rows rather than from a
    # live in-memory book; see routers/simulations.py for why that matters.
    reconstructed_at_step: int | None = None


class AgentPerformancePoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: int
    inventory: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    mark_price: float | None
    inventory_risk_score: float


class AgentPerformanceResponse(BaseModel):
    agent_id: str
    agent_type: str
    name: str
    config: dict[str, Any]
    metrics: dict[str, Any]
    risk_flags: list[Any]
    series: list[AgentPerformancePoint]


class ComparisonRow(BaseModel):
    agent_id: str
    name: str
    agent_type: str
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    sharpe_ratio: float | None
    sortino_ratio: float | None
    max_drawdown_pct: float | None
    win_rate: float | None
    closed_round_trips: int
    fill_count: int
    final_inventory: float
    peak_inventory_abs: float
    max_inventory_risk_score: float | None
    fees_paid: float
    notes: list[str]


class StepPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    step: int
    reference_price: float
    mid_price: float | None
    mark_price: float | None
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    volatility: float
    trade_count: int
    shock_fired: bool


class ComparisonResponse(BaseModel):
    simulation_id: str
    steps_run: int
    rows: list[ComparisonRow]
    market: list[StepPoint]
    shock_steps: list[int]
    # Sum of every agent's total PnL. In a closed system with no fees this is
    # zero by construction, and a non-zero value is a bug in the accounting, not
    # a profit. Surfaced in the API so the dashboard can display the check
    # rather than the reader having to trust it.
    pnl_conservation_residual: float
    total_fees_collected: float
    warnings: list[str]

    @model_validator(mode="after")
    def _sanity(self) -> "ComparisonResponse":
        return self
