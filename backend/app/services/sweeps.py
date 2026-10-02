"""Parameter sweeps that report what they are worth."""

from __future__ import annotations

import itertools
import logging
from typing import Any, Sequence

import numpy as np

from app.config import Settings
from app.services import ensembles as ensemble_service
from app.services.statistics import (
    deflated_sharpe_ratio,
    moments,
    probability_of_backtest_overfitting,
    sharpe_ratio,
)

logger = logging.getLogger("tradearenax.sweeps")


def expand_grid(parameters: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """Cartesian product of the swept parameters, in a stable order."""
    if not parameters:
        return [{}]
    keys = sorted(parameters)
    combinations = itertools.product(*(list(parameters[key]) for key in keys))
    return [dict(zip(keys, values, strict=True)) for values in combinations]


def _agents_for_cell(
    agents: list[dict[str, Any]], target_agent: str, cell: dict[str, Any]
) -> list[dict[str, Any]]:
    out = []
    for agent in agents:
        if agent["id"] == target_agent:
            out.append({**agent, "config": {**agent["config"], **cell}})
        else:
            out.append(agent)
    return out


def run_sweep(
    grid_spec: dict[str, Any],
    shared_config: dict[str, Any],
    settings: Settings,
    workers: int | None = 1,
    on_cell_complete=None,
) -> dict[str, Any]:
    """Run every cell over the same seeds and score it three ways."""
    target_agent = grid_spec["target_agent"]
    cells = expand_grid(grid_spec["parameters"])
    path_count = int(grid_spec.get("path_count", 8))
    steps = int(grid_spec.get("steps", 300))
    base_seed = int(grid_spec.get("base_seed", 42))
    in_sample_fraction = float(grid_spec.get("in_sample_fraction", 0.5))

    # The split is decided here, before a single path runs.
    in_sample_paths = max(1, int(round(path_count * in_sample_fraction)))
    if in_sample_paths >= path_count:
        in_sample_paths = path_count - 1

    results: list[dict[str, Any]] = []
    return_columns: list[np.ndarray] = []

    for index, cell in enumerate(cells):
        specs = ensemble_service.build_specs(
            shared_config={
                **shared_config,
                "agents": _agents_for_cell(shared_config["agents"], target_agent, cell),
            },
            base_seed=base_seed,
            path_count=path_count,
            steps=steps,
            settings=settings,
        )
        paths = ensemble_service.execute(specs, workers=workers)

        in_sample = paths[:in_sample_paths]
        out_sample = paths[in_sample_paths:]

        results.append(
            {
                "parameters": cell,
                "in_sample_sharpe": _mean_sharpe(in_sample, target_agent, settings),
                "out_of_sample_sharpe": _mean_sharpe(out_sample, target_agent, settings),
                "in_sample_pnl": _mean_metric(in_sample, target_agent, "total_pnl"),
                "out_of_sample_pnl": _mean_metric(out_sample, target_agent, "total_pnl"),
            }
        )
        return_columns.append(
            np.concatenate(
                [np.asarray(p["agents"][target_agent]["returns"], dtype=float) for p in paths]
            )
        )
        if on_cell_complete:
            on_cell_complete(index + 1, len(cells))

    _deflate(results, return_columns, settings)
    pbo = _overfitting(return_columns)

    ranked = [r for r in results if r["in_sample_sharpe"] is not None]
    ranked.sort(key=lambda r: r["in_sample_sharpe"], reverse=True)

    return {
        "cells": results,
        "trial_count": len(results),
        "in_sample_paths": in_sample_paths,
        "out_of_sample_paths": path_count - in_sample_paths,
        "pbo": pbo,
        "best_cell": ranked[0] if ranked else {},
        "robust_centroid": robust_centroid(ranked),
    }


def _mean_sharpe(paths: list[dict[str, Any]], agent_id: str, settings: Settings) -> float | None:
    values = [
        sharpe_ratio(
            path["agents"][agent_id]["returns"],
            settings.risk_free_per_step,
            settings.steps_per_year,
        )
        for path in paths
    ]
    usable = [v for v in values if v is not None]
    return float(np.mean(usable)) if usable else None


def _mean_metric(paths: list[dict[str, Any]], agent_id: str, metric: str) -> float | None:
    values = [
        path["agents"][agent_id].get(metric)
        for path in paths
        if path["agents"][agent_id].get(metric) is not None
    ]
    return float(np.mean(values)) if values else None


def _deflate(
    results: list[dict[str, Any]], columns: list[np.ndarray], settings: Settings
) -> None:
    """Attach a deflated Sharpe to every cell."""
    per_step = [sharpe_ratio(column, settings.risk_free_per_step) for column in columns]
    trials = [v for v in per_step if v is not None]

    for result, column, observed in zip(results, columns, per_step, strict=True):
        if observed is None or len(trials) < 2:
            result["deflated_sharpe"] = None
            continue
        skew, kurt = moments(column)
        result["deflated_sharpe"] = deflated_sharpe_ratio(
            observed_sharpe=observed,
            trial_sharpes=trials,
            n_observations=int(column.size),
            skewness=skew,
            kurtosis=kurt,
        )


def _overfitting(columns: list[np.ndarray]) -> float | None:
    if len(columns) < 2:
        return None
    length = min(column.size for column in columns)
    if length < 20:
        return None
    matrix = np.column_stack([column[:length] for column in columns])
    blocks = 8 if length >= 80 else 4
    return probability_of_backtest_overfitting(matrix, n_blocks=blocks).pbo


def robust_centroid(ranked: list[dict[str, Any]], decile: float = 0.1) -> dict[str, Any]:
    """The centre of the best decile, which is what you would actually deploy."""
    if not ranked:
        return {}
    take = max(1, int(round(len(ranked) * decile)))
    top = ranked[:take]

    centroid: dict[str, Any] = {}
    for key in top[0]["parameters"]:
        values = [cell["parameters"][key] for cell in top]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
            centroid[key] = float(np.mean(values))
        else:
            # Categorical parameters have no centre; the modal value is the honest answer rather.
            centroid[key] = max(set(values), key=values.count)

    return {
        "parameters": centroid,
        "cells_averaged": take,
        "in_sample_sharpe": float(
            np.mean([c["in_sample_sharpe"] for c in top if c["in_sample_sharpe"] is not None])
        ),
        "out_of_sample_sharpe": (
            float(
                np.mean(
                    [
                        c["out_of_sample_sharpe"]
                        for c in top
                        if c["out_of_sample_sharpe"] is not None
                    ]
                )
            )
            if any(c["out_of_sample_sharpe"] is not None for c in top)
            else None
        ),
    }


def surface(cells: list[dict[str, Any]], x_key: str, y_key: str, metric: str) -> dict[str, Any]:
    """Pack a two-parameter slice into a grid for the 3D landscape module."""
    xs = sorted({cell["parameters"][x_key] for cell in cells})
    ys = sorted({cell["parameters"][y_key] for cell in cells})
    lookup = {
        (cell["parameters"][x_key], cell["parameters"][y_key]): cell.get(metric)
        for cell in cells
    }
    values = [lookup.get((x, y)) for y in ys for x in xs]
    return {"x_key": x_key, "y_key": y_key, "metric": metric, "x": xs, "y": ys, "values": values}
