"""Request and response contracts."""

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
    # latency_in_us, latency_out_us, latency_jitter_us.
    latency: dict[str, Any] = Field(default_factory=dict)


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    simulation_id: str
    name: str
    agent_type: str
    config: dict[str, Any]
    latency_config: dict[str, Any]
    final_metrics: dict[str, Any]
    risk_flags: list[Any]
    created_at: datetime


class RunRequest(BaseModel):
    steps: int = Field(..., ge=1, le=20_000)
    # Every step of every agent is a row.
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
    # Present when the snapshot is rebuilt from stored rows rather than from a live in-memory book.
    reconstructed_at_step: int | None = None


class LiquiditySurfaceResponse(BaseModel):
    """Book depth over (time x price-relative-to-mid), as a packed grid."""

    simulation_id: str
    steps: list[int]
    mid: list[float | None]
    best_bid: list[float | None]
    best_ask: list[float | None]
    grid: list[float]
    levels: int
    width: int
    stride: int
    tick_size: float
    shock_steps: list[int]


class EnsembleAgentSpec(BaseModel):
    name: str | None = Field(None, max_length=120)
    agent_type: AgentType
    config: dict[str, Any] = Field(default_factory=dict)
    latency: dict[str, Any] = Field(default_factory=dict)


class EnsembleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    # Paths run at base_seed + i.
    path_count: int = Field(20, ge=2, le=500)
    base_seed: int = 42
    steps: int = Field(500, ge=10, le=20_000)
    price_process: PriceProcessConfig = PriceProcessConfig()
    shocks: list[VolatilityShockConfig] = Field(default_factory=list, max_length=20)
    agents: list[EnsembleAgentSpec] = Field(default_factory=list, max_length=12)
    # Worker processes.
    workers: int | None = Field(None, ge=1, le=16)


class EnsembleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    base_seed: int
    path_count: int
    steps: int
    status: str
    completed_paths: int
    shared_config: dict[str, Any]
    engine_config: dict[str, Any]
    aggregates: dict[str, Any]
    error: str | None
    created_at: datetime


class EnsembleDistribution(BaseModel):
    ensemble_id: str
    path_count: int
    steps: int
    # agent_id -> distributions, histograms, CIs, fraction positive.
    agents: dict[str, dict[str, Any]]
    market: dict[str, Any]
    # The V1 invariant across every path.
    max_pnl_conservation_residual: float


class EnsemblePaths(BaseModel):
    ensemble_id: str
    status: str
    paths: list[dict[str, Any]]


class SweepCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    # Which agent's parameters are swept.
    target_agent_index: int = Field(0, ge=0)
    # parameter -> values.
    parameters: dict[str, list[Any]] = Field(..., min_length=1)
    path_count: int = Field(8, ge=2, le=100)
    steps: int = Field(300, ge=10, le=20_000)
    base_seed: int = 42
    # Fraction of paths used to select.
    in_sample_fraction: float = Field(0.5, gt=0.0, lt=1.0)
    price_process: PriceProcessConfig = PriceProcessConfig()
    shocks: list[VolatilityShockConfig] = Field(default_factory=list, max_length=20)
    agents: list[EnsembleAgentSpec] = Field(default_factory=list, max_length=12)
    workers: int | None = Field(None, ge=1, le=16)


class SweepCell(BaseModel):
    parameters: dict[str, Any]
    in_sample_sharpe: float | None
    out_of_sample_sharpe: float | None
    deflated_sharpe: float | None
    in_sample_pnl: float | None
    out_of_sample_pnl: float | None


class SweepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    status: str
    grid_spec: dict[str, Any]
    # Probability of backtest overfitting.
    pbo: float | None
    best_cell: dict[str, Any]
    # The centre of the top decile - the parameters you would deploy.
    robust_centroid: dict[str, Any]
    trial_count: int
    error: str | None
    created_at: datetime


class SweepDetail(SweepRead):
    cells: list[SweepCell] = Field(default_factory=list)


class SweepSurface(BaseModel):
    sweep_id: str
    x_key: str
    y_key: str
    metric: str
    x: list[Any]
    y: list[Any]
    # Row-major over (y, x); null where a cell produced no number.
    values: list[float | None]
    pbo: float | None
    best_cell: dict[str, Any]
    robust_centroid: dict[str, Any]


class MicropriceResponse(BaseModel):
    """Microprice against mid, step by step, with the imbalance behind."""

    simulation_id: str
    steps: list[int]
    mid: list[float | None]
    microprice: list[float | None]
    imbalance: list[float]
    bid_quantity: list[float]
    ask_quantity: list[float]
    # RMSE of each estimator as a one-step-ahead forecast of the mid.
    forecast: dict[str, float | None]


class GreeksAttributionResponse(BaseModel):
    """Where a hedged options book's money went, step by step."""

    simulation_id: str
    # agent_id -> totals and the per-step series behind them.
    agents: dict[str, dict[str, Any]]
    names: dict[str, str]


class SpreadRow(BaseModel):
    """One agent's spread decomposition, in one role, at one horizon."""

    agent_id: str
    name: str | None = None
    role: str
    trade_count: int
    quantity: float
    effective_half_spread: float
    realised_half_spread: float
    price_impact: float
    effective_bps: float | None
    realised_bps: float | None
    impact_bps: float | None


class MicrostructureResponse(BaseModel):
    simulation_id: str
    horizons: list[int]
    default_horizon_steps: int
    # horizon -> market-wide averages, taker side.
    market: dict[str, dict[str, Any]]
    # horizon -> per-agent rows.
    by_agent: dict[str, list[SpreadRow]]


class LatencyRaceRow(BaseModel):
    step: int
    fill_us: int
    maker_agent_id: str
    maker_name: str | None = None
    taker_agent_id: str
    taker_name: str | None = None
    price: float
    quantity: float
    maker_decided_us: int
    cancel_issued_us: int
    cancel_arrival_us: int
    # How late the cancel was: positive means the fill won the race.
    margin_us: int


class LatencyRacesResponse(BaseModel):
    simulation_id: str
    step_duration_us: int
    profiles: dict[str, dict[str, Any]]
    adverse_fills: dict[str, int]
    races_recorded: int
    races_capped_at: int
    races: list[LatencyRaceRow]


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
    # Sum of every agent's total PnL.
    pnl_conservation_residual: float
    total_fees_collected: float
    warnings: list[str]

    @model_validator(mode="after")
    def _sanity(self) -> "ComparisonResponse":
        return self
