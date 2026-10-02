"""Sweeps, and the statistics that stop a sweep from lying."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.services import sweeps as sweep_service

SETTINGS = get_settings()

SHARED = {
    "price_process": {"initial_price": 100.0, "drift": 0.0, "volatility": 0.30},
    "shocks": [],
    "agents": [
        {
            "id": "agent-0",
            "name": "maker",
            "agent_type": "MARKET_MAKER",
            "config": {"spread": 0.10, "quote_size": 10.0},
            "latency": {},
        },
        {
            "id": "agent-1",
            "name": "noise",
            "agent_type": "NOISE_TRADER",
            "config": {"activity": 0.6, "random_seed": 3},
            "latency": {},
        },
    ],
}


def test_expand_grid_is_a_stable_cartesian_product():
    grid = sweep_service.expand_grid({"spread": [0.1, 0.2], "quote_size": [5, 10]})
    assert grid == [
        {"quote_size": 5, "spread": 0.1},
        {"quote_size": 5, "spread": 0.2},
        {"quote_size": 10, "spread": 0.1},
        {"quote_size": 10, "spread": 0.2},
    ]
    assert sweep_service.expand_grid({}) == [{}]


def test_grid_order_is_reproducible():
    first = sweep_service.expand_grid({"b": [1, 2], "a": [3, 4]})
    second = sweep_service.expand_grid({"a": [3, 4], "b": [1, 2]})
    assert first == second, "cell 3 must be the same cell tomorrow"


def _run(parameters, path_count=4, steps=120):
    return sweep_service.run_sweep(
        grid_spec={
            "target_agent": "agent-0",
            "parameters": parameters,
            "path_count": path_count,
            "steps": steps,
            "base_seed": 900,
            "in_sample_fraction": 0.5,
        },
        shared_config=SHARED,
        settings=SETTINGS,
        workers=1,
    )


def test_a_sweep_scores_every_cell_in_and_out_of_sample():
    outcome = _run({"spread": [0.06, 0.12, 0.24]})

    assert outcome["trial_count"] == 3
    assert outcome["in_sample_paths"] == 2 and outcome["out_of_sample_paths"] == 2
    for cell in outcome["cells"]:
        assert set(cell["parameters"]) == {"spread"}
        assert cell["in_sample_sharpe"] is not None
        assert cell["out_of_sample_sharpe"] is not None
        assert cell["deflated_sharpe"] is not None


def test_the_split_uses_different_seeds_for_selection_and_evaluation():
    """Out-of-sample means unseen, or it means nothing."""
    outcome = _run({"spread": [0.08, 0.16]}, path_count=6)
    assert outcome["in_sample_paths"] == 3
    assert outcome["out_of_sample_paths"] == 3

    # In and out of sample Sharpes should not be the identical series.
    pairs = [(c["in_sample_sharpe"], c["out_of_sample_sharpe"]) for c in outcome["cells"]]
    assert any(abs(a - b) > 1e-9 for a, b in pairs)


def test_a_sweep_reports_its_probability_of_backtest_overfitting():
    outcome = _run({"spread": [0.05, 0.10, 0.20, 0.40]})
    assert outcome["pbo"] is not None
    assert 0.0 <= outcome["pbo"] <= 1.0


def test_the_best_cell_and_the_robust_centroid_are_both_reported():
    outcome = _run({"spread": [0.04, 0.08, 0.12, 0.16, 0.20]})

    best = outcome["best_cell"]
    centroid = outcome["robust_centroid"]
    assert best["parameters"]["spread"] in (0.04, 0.08, 0.12, 0.16, 0.20)
    assert centroid["cells_averaged"] >= 1
    assert "spread" in centroid["parameters"]
    assert isinstance(centroid["parameters"]["spread"], float)


def test_deflated_sharpe_is_lower_than_the_raw_ratio_would_suggest():
    """The deflation has to actually bite, or it is decoration."""
    outcome = _run({"spread": [0.04, 0.06, 0.08, 0.10, 0.14, 0.20]})
    deflated = [c["deflated_sharpe"] for c in outcome["cells"] if c["deflated_sharpe"]]
    assert deflated, "every cell should have a deflated Sharpe"
    assert min(deflated) < 0.999, "a six-cell search cannot leave every cell certain"


def test_robust_centroid_of_a_categorical_sweep_takes_the_mode():
    ranked = [
        {"parameters": {"mode": "a"}, "in_sample_sharpe": 3.0, "out_of_sample_sharpe": 1.0},
        {"parameters": {"mode": "a"}, "in_sample_sharpe": 2.0, "out_of_sample_sharpe": 0.5},
        {"parameters": {"mode": "b"}, "in_sample_sharpe": 1.0, "out_of_sample_sharpe": 0.1},
    ]
    centroid = sweep_service.robust_centroid(ranked, decile=0.7)
    assert centroid["parameters"]["mode"] == "a"


def test_surface_packs_a_two_parameter_slice():
    cells = [
        {"parameters": {"spread": s, "quote_size": q}, "in_sample_sharpe": s * q}
        for s in (0.1, 0.2)
        for q in (5, 10)
    ]
    packed = sweep_service.surface(cells, "spread", "quote_size", "in_sample_sharpe")

    assert packed["x"] == [0.1, 0.2]
    assert packed["y"] == [5, 10]
    # Row-major over (y, x): (0.1,5), (0.2,5), (0.1,10), (0.2,10)
    assert packed["values"] == pytest.approx([0.5, 1.0, 1.0, 2.0])


def test_surface_leaves_missing_cells_as_none():
    cells = [{"parameters": {"a": 1, "b": 1}, "deflated_sharpe": 0.4}]
    packed = sweep_service.surface(
        [*cells, {"parameters": {"a": 2, "b": 1}, "deflated_sharpe": None}],
        "a",
        "b",
        "deflated_sharpe",
    )
    assert packed["values"] == [0.4, None]
