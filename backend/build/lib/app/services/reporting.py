"""Turning a finished run into numbers.

Deliberately free of the ORM. These are the measurements the HTTP layer stores
and the SDK returns, and if they lived beside the persistence code then
importing them would construct a database engine and require a Postgres driver
- which is exactly what happened the first time the SDK tried to reuse them.
Keeping them here is what lets a notebook import the same functions the API
calls without a database anywhere in the picture.
"""

from __future__ import annotations

from typing import Any

from app.config import Settings
from app.services.microstructure import (
    DEFAULT_HORIZONS,
    aggregate,
    measure_trades,
    summarise_by_agent,
)
from app.services.simulation_engine import SimulationResult



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
    """Sum of all agents' total PnL, which must be -(fees) in a closed system."""
    total = sum(s.total_pnl for s in result.summaries.values())
    fees = sum(s.fees_paid for s in result.summaries.values())
    return total + fees



def microstructure_summary(result: SimulationResult, settings: Settings) -> dict[str, Any]:
    """Spread decomposition at every reported horizon, market-wide and per agent."""
    mids = [rec.mid_price for rec in result.step_records]
    horizons = tuple(sorted({*DEFAULT_HORIZONS, settings.spread_horizon_steps}))
    measures = measure_trades(result.fills, mids, settings.tick_size, horizons=horizons)
    return {
        "horizons": list(horizons),
        "default_horizon_steps": settings.spread_horizon_steps,
        "market": {str(h): aggregate(measures, h) for h in horizons},
        "by_agent": {
            str(h): [s.as_dict() for s in summarise_by_agent(measures, h)] for h in horizons
        },
    }



def greeks_summary(result: SimulationResult) -> dict[str, Any]:
    """Attribution for every options maker in the run, if there are any."""
    from app.services.agents.options_maker import OptionsMarketMakerAgent

    out: dict[str, Any] = {}
    for agent_id, state in result.agent_states.items():
        agent = state.agent
        if isinstance(agent, OptionsMarketMakerAgent):
            out[agent_id] = agent.attribution()
    return out
