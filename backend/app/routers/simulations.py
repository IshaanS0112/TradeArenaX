"""Simulation, agent, book, and comparison endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.enums import AgentType
from app.models import Agent as AgentRow
from app.models import AgentPerformance, Simulation, SimulationStep
from app.schemas import (
    AgentCreate,
    AgentPerformanceResponse,
    AgentRead,
    ComparisonResponse,
    ComparisonRow,
    OrderBookSnapshot,
    RunRequest,
    RunSummary,
    SimulationCreate,
    SimulationRead,
)
from app.services import run_service
from app.services.agents import default_config
from app.services.book_replay import replay_book

logger = logging.getLogger("tradearenax.api")
router = APIRouter(prefix="/simulations", tags=["simulations"])


def _get_simulation(db: Session, simulation_id: str) -> Simulation:
    sim = db.get(Simulation, simulation_id)
    if sim is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "simulation not found")
    return sim


def _require_completed(sim: Simulation) -> None:
    if sim.status != "COMPLETED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "this simulation has not been run yet - POST /simulations/{id}/run first",
        )


# --------------------------------------------------------------------- create
@router.post("", response_model=SimulationRead, status_code=status.HTTP_201_CREATED)
def create_simulation(payload: SimulationCreate, db: Session = Depends(get_db)):
    settings = get_settings()
    sim = Simulation(
        name=payload.name,
        price_process_config=payload.price_process.model_dump(),
        volatility_shock_config={"shocks": [s.model_dump() for s in payload.shocks]},
        engine_config=run_service.engine_config_snapshot(settings),
        status="CREATED",
    )
    db.add(sim)
    db.commit()
    db.refresh(sim)
    return sim


@router.get("", response_model=list[SimulationRead])
def list_simulations(db: Session = Depends(get_db)):
    return list(db.scalars(select(Simulation).order_by(Simulation.created_at.desc())).all())


@router.get("/{simulation_id}", response_model=SimulationRead)
def get_simulation(simulation_id: str, db: Session = Depends(get_db)):
    return _get_simulation(db, simulation_id)


@router.delete("/{simulation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_simulation(simulation_id: str, db: Session = Depends(get_db)):
    sim = _get_simulation(db, simulation_id)
    db.delete(sim)
    db.commit()


# --------------------------------------------------------------------- agents
@router.post(
    "/{simulation_id}/agents",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
)
def add_agent(simulation_id: str, payload: AgentCreate, db: Session = Depends(get_db)):
    sim = _get_simulation(db, simulation_id)
    settings = get_settings()

    existing = len(sim.agents)
    if existing >= settings.max_agents_per_simulation:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"at most {settings.max_agents_per_simulation} agents per simulation",
        )

    try:
        resolved = run_service.resolve_agent_config(payload.agent_type, payload.config)
    except ValueError as exc:
        # A misspelled config key is a 422, not a silently ignored default: a run
        # whose parameters are not what the caller asked for is not reproducible.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    row = AgentRow(
        simulation_id=sim.id,
        name=payload.name or f"{payload.agent_type}-{existing + 1}",
        agent_type=str(payload.agent_type),
        config=resolved,
    )
    db.add(row)
    # Results from a previous run no longer describe this agent set.
    if sim.status == "COMPLETED":
        sim.status = "CREATED"
        sim.run_summary = {}
    db.commit()
    db.refresh(row)
    return row


@router.get("/{simulation_id}/agents", response_model=list[AgentRead])
def list_agents(simulation_id: str, db: Session = Depends(get_db)):
    _get_simulation(db, simulation_id)
    return list(
        db.scalars(
            select(AgentRow)
            .where(AgentRow.simulation_id == simulation_id)
            .order_by(AgentRow.created_at)
        ).all()
    )


# ------------------------------------------------------------------------ run
@router.post("/{simulation_id}/run", response_model=RunSummary)
def run(simulation_id: str, payload: RunRequest, db: Session = Depends(get_db)):
    """Run N steps.

    Declared ``def`` rather than ``async def`` deliberately: the simulation loop
    is CPU-bound Python, so FastAPI runs it in a threadpool and the event loop
    stays free to serve other requests. An ``async def`` here would block every
    other client for the duration of the run.
    """
    sim = _get_simulation(db, simulation_id)
    settings = get_settings()

    shocks = sim.volatility_shock_config.get("shocks", [])
    late = [s["step"] for s in shocks if s["step"] > payload.steps]
    if late:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"shocks are scheduled at steps {late}, past the end of a "
            f"{payload.steps}-step run - they would never fire",
        )

    try:
        result, names = run_service.run_simulation(
            db=db,
            simulation=sim,
            steps=payload.steps,
            persist_every_n_steps=payload.persist_every_n_steps,
            settings=settings,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    summary = sim.run_summary
    return RunSummary(
        simulation_id=sim.id,
        steps_run=summary["steps_run"],
        total_trades=summary["total_trades"],
        total_orders=summary["total_orders"],
        configured_volatility=summary["configured_volatility"],
        realized_volatility=summary["realized_volatility"],
        steps_with_no_two_sided_book=summary["steps_with_no_two_sided_book"],
        final_reference_price=summary["final_reference_price"],
        final_mid_price=summary["final_mid_price"],
        warnings=summary["warnings"],
        agent_metrics=summary["agent_metrics"],
    )


# ------------------------------------------------------------------ book state
@router.get("/{simulation_id}/order-book/snapshot", response_model=OrderBookSnapshot)
def order_book_snapshot(
    simulation_id: str,
    step: int | None = Query(
        None,
        ge=1,
        description="Rebuild the book as of this step. Omit for the end of the run.",
    ),
    levels: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """The book, rebuilt from the persisted order stream.

    See services/book_replay.py: the reconstruction is exact, not an estimate,
    because the stored stream includes submission sequence and cancel step.
    """
    sim = _get_simulation(db, simulation_id)
    _require_completed(sim)

    if step is not None and step > sim.duration_steps:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"step {step} is past the end of this {sim.duration_steps}-step run",
        )

    replay = replay_book(
        db,
        simulation_id=sim.id,
        tick_size=float(sim.engine_config.get("tick_size", get_settings().tick_size)),
        up_to_step=step,
    )
    snapshot = replay.book.snapshot(levels=levels)
    return OrderBookSnapshot(**snapshot, reconstructed_at_step=replay.step)


# ---------------------------------------------------------------- performance
@router.get(
    "/{simulation_id}/agents/{agent_id}/performance",
    response_model=AgentPerformanceResponse,
)
def agent_performance(simulation_id: str, agent_id: str, db: Session = Depends(get_db)):
    sim = _get_simulation(db, simulation_id)
    row = db.get(AgentRow, agent_id)
    if row is None or row.simulation_id != sim.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found in this simulation")
    _require_completed(sim)

    series = list(
        db.scalars(
            select(AgentPerformance)
            .where(AgentPerformance.agent_id == agent_id)
            .order_by(AgentPerformance.step)
        ).all()
    )
    return AgentPerformanceResponse(
        agent_id=row.id,
        agent_type=row.agent_type,
        name=row.name,
        config=row.config,
        metrics=row.final_metrics,
        risk_flags=row.risk_flags,
        series=series,
    )


@router.get("/{simulation_id}/comparison", response_model=ComparisonResponse)
def comparison(simulation_id: str, db: Session = Depends(get_db)):
    sim = _get_simulation(db, simulation_id)
    _require_completed(sim)

    agents = list(
        db.scalars(
            select(AgentRow)
            .where(AgentRow.simulation_id == sim.id)
            .order_by(AgentRow.created_at)
        ).all()
    )
    steps = list(
        db.scalars(
            select(SimulationStep)
            .where(SimulationStep.simulation_id == sim.id)
            .order_by(SimulationStep.step)
        ).all()
    )

    rows = []
    for a in agents:
        m = a.final_metrics or {}
        rows.append(
            ComparisonRow(
                agent_id=a.id,
                name=a.name,
                agent_type=a.agent_type,
                total_pnl=m.get("total_pnl", 0.0),
                realized_pnl=m.get("realized_pnl", 0.0),
                unrealized_pnl=m.get("unrealized_pnl", 0.0),
                sharpe_ratio=m.get("sharpe_ratio"),
                sortino_ratio=m.get("sortino_ratio"),
                max_drawdown_pct=m.get("max_drawdown_pct"),
                win_rate=m.get("win_rate"),
                closed_round_trips=m.get("closed_round_trips", 0),
                fill_count=m.get("fill_count", 0),
                final_inventory=m.get("final_inventory", 0.0),
                peak_inventory_abs=m.get("peak_inventory_abs", 0.0),
                max_inventory_risk_score=m.get("max_inventory_risk_score"),
                fees_paid=m.get("fees_paid", 0.0),
                notes=m.get("notes", []),
            )
        )

    summary = sim.run_summary or {}
    return ComparisonResponse(
        simulation_id=sim.id,
        steps_run=sim.duration_steps,
        rows=sorted(rows, key=lambda r: r.total_pnl, reverse=True),
        market=steps,
        shock_steps=[s["step"] for s in sim.volatility_shock_config.get("shocks", [])],
        pnl_conservation_residual=summary.get("pnl_conservation_residual", 0.0),
        total_fees_collected=summary.get("total_fees_collected", 0.0),
        warnings=summary.get("warnings", []),
    )


meta_router = APIRouter(prefix="/meta", tags=["meta"])


@meta_router.get("/agent-defaults")
def agent_defaults() -> dict[str, dict]:
    """Every agent archetype's full parameter set with its default value.

    The dashboard builds its config forms from this, so a parameter added to an
    agent shows up in the UI without a frontend change - and, more importantly,
    a reader can see the entire tunable surface of each strategy in one place.
    """
    return {str(t): default_config(t) for t in AgentType}


@meta_router.get("/engine-config")
def engine_config() -> dict:
    """The engine constants in force, and the formulas that consume them.

    Exposed as an endpoint because the honest claim this project makes - that the
    metrics are computed rather than asserted - is only checkable if the
    parameters behind them are visible without reading the source.
    """
    settings = get_settings()
    return {
        "engine": run_service.engine_config_snapshot(settings),
        "derived": {
            "dt_years_per_step": settings.dt,
            "risk_free_per_step": settings.risk_free_per_step,
        },
        "formulas": {
            "price_process": "S_{t+1} = S_t * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)",
            "market_maker_quotes": (
                "bid = fair_value - half_spread - k*inventory; "
                "ask = fair_value + half_spread - k*inventory"
            ),
            "momentum_signal": "(P_t - P_{t-N}) / P_{t-N}",
            "mean_reversion_signal": "(P_t - MA_window) / SD_window",
            "realized_pnl": "FIFO lot matching, position flips split across lots",
            "unrealized_pnl": "(mark - weighted_avg_open_entry) * inventory",
            "sharpe": (
                "mean(r - rf) / sd(r - rf) * sqrt(steps_per_year), "
                "where r_t = d(equity_t)/equity_{t-1} and equity = capital_base + PnL"
            ),
            "max_drawdown": "max(running_peak(equity) - equity) / peak_at_that_point",
            "win_rate": "profitable closed round trips / closed round trips",
            "inventory_risk": "|inventory| / max_inventory; > 1.0 forces partial liquidation",
        },
        "authenticity": (
            "The reference price path is synthetic (GBM). The order book matching, "
            "agent logic, PnL accounting, and risk metrics are real computations "
            "over that path. No live market data, no broker, no capital."
        ),
    }
