"""Persistence layer around a run: DB rows in, engine out, DB rows back.

Kept out of the router so the engine can be driven from a script or a test with
no HTTP layer, and out of the engine so the engine has no database dependency.
The engine does not know what a session is; this module is the only place that
knows both.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.enums import OrderType
from app.models import Agent as AgentRow
from app.models import AgentPerformance, Order, Simulation, SimulationStep, Trade
from app.services.agents import build_agent
from app.services.simulation_engine import (
    SimulationEngine,
    SimulationResult,
    build_price_process,
)

logger = logging.getLogger("tradearenax.run")


def engine_config_snapshot(settings: Settings) -> dict[str, Any]:
    """The constants that turn the same agents on the same path into a number.

    Persisted with every run. Without it a stored Sharpe of 1.8 cannot be
    checked, because Sharpe depends on capital_base and steps_per_year, and
    neither is recoverable from the PnL series.
    """
    return {
        "tick_size": settings.tick_size,
        "min_order_quantity": settings.min_order_quantity,
        "maker_fee_bps": settings.maker_fee_bps,
        "taker_fee_bps": settings.taker_fee_bps,
        "capital_base": settings.capital_base,
        "risk_free_rate_annual": settings.risk_free_rate_annual,
        "steps_per_year": settings.steps_per_year,
        "self_trade_prevention": str(settings.self_trade_prevention),
        "liquidation_fraction": settings.liquidation_fraction,
    }


def resolve_agent_config(agent_type: str, config: dict[str, Any]) -> dict[str, Any]:
    """Validate an agent config and fill in defaults, raising on unknown keys."""
    return build_agent("validation-probe", agent_type, config).config


def run_simulation(
    db: Session,
    simulation: Simulation,
    steps: int,
    persist_every_n_steps: int = 1,
    settings: Settings | None = None,
) -> tuple[SimulationResult, dict[str, str]]:
    """Execute a simulation and persist orders, trades, steps, and performance.

    Re-running an existing simulation clears its previous results first. The
    alternative - appending - produces a performance table with two step 1 rows
    and charts that zigzag back in time.
    """
    settings = settings or get_settings()
    agent_rows: list[AgentRow] = list(
        db.scalars(select(AgentRow).where(AgentRow.simulation_id == simulation.id)).all()
    )
    if not agent_rows:
        raise ValueError("add at least one agent before running a simulation")

    _clear_previous_results(db, simulation.id)

    price_process = build_price_process(
        {
            **simulation.price_process_config,
            "shocks": simulation.volatility_shock_config.get("shocks", []),
        },
        settings,
    )
    engine = SimulationEngine(price_process=price_process, settings=settings)

    for row in agent_rows:
        engine.add_agent(agent_id=row.id, agent_type=row.agent_type, config=row.config)

    result = engine.run(steps)

    _persist_orders(db, simulation.id, result)
    _persist_trades(db, simulation.id, result)
    _persist_steps(db, simulation.id, result, persist_every_n_steps)
    _persist_performance(db, simulation.id, result, persist_every_n_steps)

    names = {row.id: row.name for row in agent_rows}
    for row in agent_rows:
        summary = result.summaries[row.id]
        row.final_metrics = summary.as_dict()
        row.risk_flags = list(result.agent_states[row.id].agent.flags)

    simulation.duration_steps = result.steps_run
    simulation.status = "COMPLETED"
    simulation.engine_config = engine_config_snapshot(settings)
    simulation.run_summary = build_run_summary(simulation.id, result, names)

    db.commit()
    return result, names


def build_run_summary(
    simulation_id: str, result: SimulationResult, names: dict[str, str]
) -> dict[str, Any]:
    final_mid = result.step_records[-1].mid_price if result.step_records else None
    return {
        "simulation_id": simulation_id,
        "steps_run": result.steps_run,
        "total_trades": len(result.fills),
        "total_orders": len(result.orders),
        "configured_volatility": result.configured_volatility,
        "realized_volatility": result.realized_volatility,
        "steps_with_no_two_sided_book": result.steps_with_no_two_sided_book,
        "final_reference_price": result.reference_path[-1],
        "final_mid_price": final_mid,
        "warnings": result.warnings,
        "pnl_conservation_residual": pnl_conservation_residual(result),
        "total_fees_collected": sum(s.fees_paid for s in result.summaries.values()),
        "agent_metrics": {
            agent_id: {"name": names.get(agent_id, agent_id), **summary.as_dict()}
            for agent_id, summary in result.summaries.items()
        },
    }


def pnl_conservation_residual(result: SimulationResult) -> float:
    """Sum of all agents' total PnL, which must be -(fees) in a closed system.

    Every trade moves value between two participants in this simulation; nothing
    enters or leaves except fees. So the sum of total PnL across agents is zero
    when fees are off, and exactly minus the fees collected when they are on.
    This is the strongest single check on the accounting layer, and it runs on
    every run rather than only in tests.
    """
    total = sum(s.total_pnl for s in result.summaries.values())
    fees = sum(s.fees_paid for s in result.summaries.values())
    return total + fees


def _clear_previous_results(db: Session, simulation_id: str) -> None:
    for model in (AgentPerformance, Trade, Order, SimulationStep):
        db.execute(delete(model).where(model.simulation_id == simulation_id))
    db.flush()


def _persist_orders(db: Session, simulation_id: str, result: SimulationResult) -> None:
    tick = get_settings().tick_size
    rows = [
        {
            "simulation_id": simulation_id,
            "agent_id": order.agent_id,
            "engine_order_id": order.order_id,
            "sequence": order.sequence,
            "step": order.step,
            "side": str(order.side),
            "order_type": str(order.order_type),
            # A MARKET order carries a sentinel internal price that is not a
            # real price; storing it would put 1e18 in a price column.
            "price": None
            if order.order_type is OrderType.MARKET
            else round(order.price_ticks * tick, 10),
            "quantity": order.quantity,
            "filled_quantity": order.filled_quantity,
            "status": str(order.status),
            "cancelled_at_step": order.cancelled_at_step,
            "cancelled_at_sequence": order.cancelled_at_sequence,
        }
        for order in result.orders
    ]
    if rows:
        db.bulk_insert_mappings(Order, rows)


def _persist_trades(db: Session, simulation_id: str, result: SimulationResult) -> None:
    tick = get_settings().tick_size
    rows = [
        {
            "simulation_id": simulation_id,
            "step": fill.step,
            "buy_order_id": fill.buy_order_id,
            "sell_order_id": fill.sell_order_id,
            "buy_agent_id": fill.buy_agent_id,
            "sell_agent_id": fill.sell_agent_id,
            "aggressor_side": str(fill.aggressor_side),
            "price": round(fill.price_ticks * tick, 10),
            "quantity": fill.quantity,
        }
        for fill in result.fills
    ]
    if rows:
        db.bulk_insert_mappings(Trade, rows)


def _persist_steps(
    db: Session, simulation_id: str, result: SimulationResult, every: int
) -> None:
    rows = [
        {
            "simulation_id": simulation_id,
            "step": rec.step,
            "reference_price": rec.reference_price,
            "mid_price": rec.mid_price,
            "mark_price": rec.mark_price,
            "best_bid": rec.best_bid,
            "best_ask": rec.best_ask,
            "spread": rec.spread,
            "volatility": rec.volatility,
            "trade_count": rec.trade_count,
            "shock_fired": rec.shock_fired,
        }
        # A shock step is never downsampled away: it is the one step a reader
        # will look for, and dropping it because it fell between samples makes
        # the chart lie about what happened.
        for rec in result.step_records
        if rec.step % every == 0 or rec.step == result.steps_run or rec.shock_fired
    ]
    if rows:
        db.bulk_insert_mappings(SimulationStep, rows)


def _persist_performance(
    db: Session, simulation_id: str, result: SimulationResult, every: int
) -> None:
    # The mark actually used for the unrealized leg, not the (possibly null) mid.
    marks = {rec.step: rec.mark_price for rec in result.step_records}
    rows = []
    for agent_id, state in result.agent_states.items():
        limit = state.agent.max_inventory
        for idx, total in enumerate(state.pnl_series):
            step = idx + 1
            if step % every != 0 and step != result.steps_run:
                continue
            inventory = state.inventory_series[idx]
            rows.append(
                {
                    "simulation_id": simulation_id,
                    "agent_id": agent_id,
                    "step": step,
                    "inventory": inventory,
                    "realized_pnl": state.realized_series[idx],
                    "unrealized_pnl": state.unrealized_series[idx],
                    "total_pnl": total,
                    "mark_price": marks.get(step),
                    "inventory_risk_score": abs(inventory) / limit if limit else 0.0,
                }
            )
    if rows:
        db.bulk_insert_mappings(AgentPerformance, rows)


def count_rows(db: Session, model, simulation_id: str) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(model).where(model.simulation_id == simulation_id)
        )
        or 0
    )
