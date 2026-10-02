"""Ensembles: distributions instead of point estimates."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import ensembles as ensemble_service
from app.services.event_engine import EventDrivenEngine
from app.services.simulation_engine import build_price_process

SETTINGS = get_settings()

NOISE_PAIR = [
    {
        "id": "noise-a",
        "name": "noise A",
        "agent_type": "NOISE_TRADER",
        "config": {"activity": 0.6, "order_size": 5.0, "random_seed": 11},
        "latency": {},
    },
    {
        "id": "noise-b",
        "name": "noise B",
        "agent_type": "NOISE_TRADER",
        "config": {"activity": 0.6, "order_size": 5.0, "random_seed": 29},
        "latency": {},
    },
]

SHARED = {
    "price_process": {"initial_price": 100.0, "drift": 0.0, "volatility": 0.25},
    "shocks": [],
    "agents": NOISE_PAIR,
}


def _paths(path_count=24, steps=150, shared=None, base_seed=1_000):
    specs = ensemble_service.build_specs(
        shared_config=shared or SHARED,
        base_seed=base_seed,
        path_count=path_count,
        steps=steps,
        settings=SETTINGS,
    )
    return ensemble_service.execute(specs, workers=1)


def test_a_path_reproduces_a_single_run_on_the_same_seed():
    """An ensemble path is not a different engine, just a different seed."""
    spec = ensemble_service.build_specs(SHARED, base_seed=77, path_count=1, steps=120, settings=SETTINGS)[0]
    path = ensemble_service.run_path(spec)

    process = build_price_process(
        {**SHARED["price_process"], "random_seed": 77, "shocks": []}, SETTINGS
    )
    engine = EventDrivenEngine(price_process=process, settings=SETTINGS)
    # From the spec's own agent configs: build_specs advances each stochastic agent's seed per path.
    for agent in spec["agents"]:
        engine.add_agent(agent["id"], agent["agent_type"], agent["config"])
    result = engine.run(120)

    assert path["total_trades"] == len(result.fills)
    for agent_id, summary in result.summaries.items():
        assert path["agents"][agent_id]["total_pnl"] == pytest.approx(summary.total_pnl)


def test_seeds_are_base_plus_index_and_produce_different_paths():
    specs = ensemble_service.build_specs(SHARED, base_seed=500, path_count=4, steps=60, settings=SETTINGS)
    assert [s["seed"] for s in specs] == [500, 501, 502, 503]

    paths = ensemble_service.execute(specs, workers=1)
    finals = {round(p["final_reference_price"], 6) for p in paths}
    assert len(finals) == 4, "different seeds must give different paths"


def test_noise_trader_mean_pnl_confidence_interval_contains_zero():
    """The engine-bias check."""
    paths = _paths(path_count=40, steps=180)
    aggregates = ensemble_service.aggregate_paths(
        paths, {a["id"]: a["name"] for a in NOISE_PAIR}, SETTINGS
    )

    for agent_id in ("noise-a", "noise-b"):
        agent = aggregates["agents"][agent_id]
        assert agent["pnl_ci_contains_zero"], (
            f"{agent_id} mean PnL CI "
            f"[{agent['pnl_ci_lower']:.2f}, {agent['pnl_ci_upper']:.2f}] excludes zero - "
            "the engine is favouring an agent"
        )
        assert 0.2 <= agent["fraction_positive"] <= 0.8


def test_pnl_conservation_holds_on_every_path():
    paths = _paths(path_count=12, steps=120)
    aggregates = ensemble_service.aggregate_paths(paths, {}, SETTINGS)
    assert aggregates["max_pnl_conservation_residual"] < 1e-6


def test_distributions_report_percentiles_and_histograms():
    paths = _paths(path_count=16, steps=120)
    aggregates = ensemble_service.aggregate_paths(paths, {}, SETTINGS)
    agent = aggregates["agents"]["noise-a"]

    pnl = agent["distributions"]["total_pnl"]
    assert pnl["count"] == 16
    assert pnl["p5"] <= pnl["median"] <= pnl["p95"]
    assert agent["histograms"]["total_pnl"]["counts"], "a histogram of 16 paths has bars"
    assert agent["within_path_sharpe"]["resamples"] > 0


def test_a_metric_no_path_could_compute_stays_none():
    """An agent with no closed round trip has no win rate - on any path."""
    paths = _paths(path_count=6, steps=40)
    for path in paths:
        for metrics in path["agents"].values():
            metrics["max_drawdown_pct"] = None
    aggregates = ensemble_service.aggregate_paths(paths, {}, SETTINGS)
    drawdown = aggregates["agents"]["noise-a"]["distributions"]["max_drawdown_pct"]
    assert drawdown["count"] == 0
    assert drawdown["mean"] is None


def test_path_rows_are_compact_and_named():
    paths = _paths(path_count=5, steps=60)
    rows = ensemble_service.path_rows(paths, {a["id"]: a["name"] for a in NOISE_PAIR})
    assert [r["path_index"] for r in rows] == [0, 1, 2, 3, 4]
    assert rows[0]["agents"]["noise-a"]["name"] == "noise A"
    assert "returns" not in rows[0]["agents"]["noise-a"], "rows stay small"
