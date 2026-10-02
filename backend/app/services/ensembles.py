"""Monte Carlo ensembles: the same configuration, many seeds."""

from __future__ import annotations

import logging
import math
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import numpy as np

from app.config import Settings, get_settings
from app.services.event_engine import EventDrivenEngine
from app.services.latency import LatencyProfile
from app.services.microstructure import aggregate, measure_trades
from app.services.simulation_engine import build_price_process
from app.services.statistics import (
    bootstrap_mean_ci,
    bootstrap_sharpe_ci,
    sharpe_ratio,
)

logger = logging.getLogger("tradearenax.ensembles")

# Metrics whose distribution is reported for every agent.
DISTRIBUTION_METRICS = (
    "total_pnl",
    "realized_pnl",
    "sharpe_ratio",
    "max_drawdown_pct",
    "fill_count",
    "realised_half_spread",
    "price_impact",
)


def run_path(spec: dict[str, Any]) -> dict[str, Any]:
    """Run one path and return plain data."""
    settings = Settings(**spec["settings"])
    steps = int(spec["steps"])
    seed = int(spec["seed"])

    price_config = {**spec["price_process"], "random_seed": seed, "shocks": spec["shocks"]}
    process = build_price_process(price_config, settings)
    engine = EventDrivenEngine(price_process=process, settings=settings)

    for agent in spec["agents"]:
        engine.add_agent(
            agent_id=agent["id"],
            agent_type=agent["agent_type"],
            config=agent["config"],
            latency=LatencyProfile.from_config(agent.get("latency")),
        )

    result = engine.run(steps)

    mids = [record.mid_price for record in result.step_records]
    horizon = settings.spread_horizon_steps
    measures = measure_trades(result.fills, mids, settings.tick_size, horizons=(horizon,))
    market = aggregate(measures, horizon)

    per_agent: dict[str, Any] = {}
    for agent_id, summary in result.summaries.items():
        state = result.agent_states[agent_id]
        returns = _step_returns(state.pnl_series, settings.capital_base)
        agent_measures = [
            m for m in measures if m.maker_agent_id == agent_id or m.taker_agent_id == agent_id
        ]
        maker_side = aggregate(
            [m for m in agent_measures if m.maker_agent_id == agent_id], horizon
        )
        per_agent[agent_id] = {
            **summary.as_dict(),
            "returns": returns,
            "realised_half_spread": maker_side["realised_half_spread"],
            "price_impact": maker_side["price_impact"],
        }

    return {
        "seed": seed,
        "path_index": int(spec["path_index"]),
        "steps_run": result.steps_run,
        "total_trades": len(result.fills),
        "final_reference_price": result.reference_path[-1],
        "realized_volatility": result.realized_volatility,
        "market_microstructure": market,
        "agents": per_agent,
        "pnl_conservation_residual": sum(s.total_pnl for s in result.summaries.values())
        + sum(s.fees_paid for s in result.summaries.values()),
    }


def _step_returns(pnl_series: list[float], capital_base: float) -> list[float]:
    """Equity returns, the same definition performance_metrics uses."""
    returns: list[float] = []
    previous_equity = capital_base
    for pnl in pnl_series:
        equity = capital_base + pnl
        if previous_equity != 0:
            returns.append((equity - previous_equity) / previous_equity)
        previous_equity = equity
    return returns


def build_specs(
    shared_config: dict[str, Any],
    base_seed: int,
    path_count: int,
    steps: int,
    settings: Settings,
) -> list[dict[str, Any]]:
    """One spec per path: same configuration, seed ``base_seed + i``."""
    settings_blob = settings.model_dump()
    specs = []
    for index in range(path_count):
        agents = []
        for position, agent in enumerate(shared_config.get("agents", [])):
            config = dict(agent.get("config", {}))
            if "random_seed" in config:
                config["random_seed"] = int(config["random_seed"]) + 10_000 * index + position
            agents.append({**agent, "config": config})
        specs.append(
            {
                "settings": settings_blob,
                "steps": steps,
                "seed": base_seed + index,
                "path_index": index,
                "price_process": shared_config.get("price_process", {}),
                "shocks": shared_config.get("shocks", []),
                "agents": agents,
            }
        )
    return specs


def execute(
    specs: list[dict[str, Any]],
    workers: int | None = None,
    on_path_complete=None,
) -> list[dict[str, Any]]:
    """Run every path, in parallel when there is enough work to justify."""
    resolved_workers = workers if workers is not None else min(os.cpu_count() or 1, 8)
    results: list[dict[str, Any]] = []

    if resolved_workers <= 1 or len(specs) < 4:
        for spec in specs:
            results.append(run_path(spec))
            if on_path_complete:
                on_path_complete(len(results))
        return results

    with ProcessPoolExecutor(max_workers=resolved_workers) as pool:
        for path in pool.map(run_path, specs):
            results.append(path)
            if on_path_complete:
                on_path_complete(len(results))

    results.sort(key=lambda p: p["path_index"])
    return results


# ------------------------------------------------------------------ aggregates
def _percentiles(values: list[float]) -> dict[str, float | None]:
    usable = [v for v in values if v is not None and math.isfinite(v)]
    if not usable:
        # Every path declined to produce this metric - an agent that never closed a trade has no win.
        return {"count": 0, "mean": None, "median": None, "p5": None, "p95": None,
                "min": None, "max": None, "std": None}
    array = np.asarray(usable, dtype=float)
    return {
        "count": int(array.size),
        "mean": float(array.mean()),
        "median": float(np.median(array)),
        "p5": float(np.percentile(array, 5)),
        "p95": float(np.percentile(array, 95)),
        "min": float(array.min()),
        "max": float(array.max()),
        "std": float(array.std(ddof=1)) if array.size > 1 else 0.0,
    }


def _histogram(values: list[float], bins: int = 20) -> dict[str, Any]:
    usable = [v for v in values if v is not None and math.isfinite(v)]
    if len(usable) < 2:
        return {"bins": [], "counts": []}
    counts, edges = np.histogram(np.asarray(usable, dtype=float), bins=bins)
    return {"bins": [float(e) for e in edges], "counts": [int(c) for c in counts]}


def aggregate_paths(
    paths: list[dict[str, Any]],
    agent_names: dict[str, str],
    settings: Settings,
    resamples: int = 4_000,
) -> dict[str, Any]:
    """Distributions, confidence intervals and the fraction-positive check."""
    if not paths:
        return {"path_count": 0, "agents": {}}

    agent_ids = list(paths[0]["agents"])
    per_agent: dict[str, Any] = {}

    for agent_id in agent_ids:
        metrics = {
            name: [path["agents"][agent_id].get(name) for path in paths]
            for name in DISTRIBUTION_METRICS
        }
        pnl_values = [v for v in metrics["total_pnl"] if v is not None]

        mean_pnl, pnl_lower, pnl_upper = bootstrap_mean_ci(
            pnl_values, resamples=resamples, seed=settings.default_random_seed
        )

        # The within-path interval: the median path's own return series, block bootstrapped.
        median_index = int(np.argsort(pnl_values)[len(pnl_values) // 2]) if pnl_values else 0
        median_returns = paths[median_index]["agents"][agent_id]["returns"]
        within = bootstrap_sharpe_ci(
            median_returns,
            risk_free_per_step=settings.risk_free_per_step,
            steps_per_year=settings.steps_per_year,
            resamples=min(resamples, 2_000),
            seed=settings.default_random_seed,
        )

        sharpes = [
            sharpe_ratio(
                path["agents"][agent_id]["returns"],
                settings.risk_free_per_step,
                settings.steps_per_year,
            )
            for path in paths
        ]
        mean_sharpe, sharpe_lower, sharpe_upper = bootstrap_mean_ci(
            [s for s in sharpes if s is not None],
            resamples=resamples,
            seed=settings.default_random_seed + 1,
        )

        per_agent[agent_id] = {
            "name": agent_names.get(agent_id, agent_id),
            "distributions": {
                name: _percentiles([v for v in values if v is not None])
                for name, values in metrics.items()
            },
            "histograms": {
                "total_pnl": _histogram(pnl_values),
                "sharpe_ratio": _histogram([s for s in sharpes if s is not None]),
            },
            # For a market maker this should be high and tight.
            "fraction_positive": (
                float(np.mean([1.0 if v > 0 else 0.0 for v in pnl_values]))
                if pnl_values
                else None
            ),
            "mean_pnl": mean_pnl,
            "pnl_ci_lower": pnl_lower,
            "pnl_ci_upper": pnl_upper,
            "pnl_ci_contains_zero": (
                None
                if pnl_lower is None or pnl_upper is None
                else bool(pnl_lower <= 0.0 <= pnl_upper)
            ),
            "mean_sharpe": mean_sharpe,
            "sharpe_ci_lower": sharpe_lower,
            "sharpe_ci_upper": sharpe_upper,
            "within_path_sharpe": within.as_dict(),
        }

    residuals = [abs(path["pnl_conservation_residual"]) for path in paths]
    return {
        "path_count": len(paths),
        "steps": paths[0]["steps_run"],
        "agents": per_agent,
        "market": {
            "trades": _percentiles([float(p["total_trades"]) for p in paths]),
            "realised_half_spread": _percentiles(
                [p["market_microstructure"]["realised_half_spread"] for p in paths]
            ),
            "price_impact": _percentiles(
                [p["market_microstructure"]["price_impact"] for p in paths]
            ),
        },
        # The V1 invariant, across every path of the ensemble.
        "max_pnl_conservation_residual": max(residuals) if residuals else 0.0,
    }


def path_rows(paths: list[dict[str, Any]], agent_names: dict[str, str]) -> list[dict[str, Any]]:
    """One compact summary row per path, for the table under the fan chart."""
    rows = []
    for path in paths:
        rows.append(
            {
                "path_index": path["path_index"],
                "seed": path["seed"],
                "total_trades": path["total_trades"],
                "final_reference_price": path["final_reference_price"],
                "realized_volatility": path["realized_volatility"],
                "agents": {
                    agent_id: {
                        "name": agent_names.get(agent_id, agent_id),
                        "total_pnl": metrics.get("total_pnl"),
                        "sharpe_ratio": metrics.get("sharpe_ratio"),
                        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
                        "fill_count": metrics.get("fill_count"),
                    }
                    for agent_id, metrics in path["agents"].items()
                },
            }
        )
    return rows


def default_settings() -> Settings:
    return get_settings()
