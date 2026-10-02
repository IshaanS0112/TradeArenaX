"""Sweep endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import SessionLocal, get_db
from app.models import Sweep, SweepResult
from app.schemas import SweepCell, SweepCreate, SweepDetail, SweepRead, SweepSurface
from app.services import sweeps as sweep_service
from app.services.agents import build_agent
from app.services.latency import LatencyProfile
from app.services.run_service import engine_config_snapshot

logger = logging.getLogger("tradearenax.sweeps.api")
router = APIRouter(prefix="/sweeps", tags=["sweeps"])

# Cap on the grid.
MAX_CELLS = 400


def _get(db: Session, sweep_id: str) -> Sweep:
    row = db.get(Sweep, sweep_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "sweep not found")
    return row


def execute_sweep(sweep_id: str, workers: int | None = 1) -> None:
    settings = get_settings()
    db = SessionLocal()
    try:
        row = db.get(Sweep, sweep_id)
        if row is None:  # pragma: no cover - deleted mid-flight
            return
        row.status = "RUNNING"
        db.commit()

        outcome = sweep_service.run_sweep(
            grid_spec=row.grid_spec,
            shared_config=row.shared_config,
            settings=settings,
            workers=workers,
        )

        db.add_all(
            SweepResult(
                sweep_id=row.id,
                parameters=cell["parameters"],
                in_sample_sharpe=cell["in_sample_sharpe"],
                out_of_sample_sharpe=cell["out_of_sample_sharpe"],
                deflated_sharpe=cell["deflated_sharpe"],
                in_sample_pnl=cell["in_sample_pnl"],
                out_of_sample_pnl=cell["out_of_sample_pnl"],
                trial_count=outcome["trial_count"],
            )
            for cell in outcome["cells"]
        )
        row.pbo = outcome["pbo"]
        row.best_cell = outcome["best_cell"]
        row.robust_centroid = outcome["robust_centroid"]
        row.trial_count = outcome["trial_count"]
        row.status = "COMPLETED"
        db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("sweep %s failed", sweep_id)
        db.rollback()
        row = db.get(Sweep, sweep_id)
        if row is not None:
            row.status = "FAILED"
            row.error = str(exc)[:500]
            db.commit()
    finally:
        db.close()


@router.post("", response_model=SweepRead, status_code=status.HTTP_202_ACCEPTED)
def create_sweep(
    payload: SweepCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if not payload.agents:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "a sweep needs agents")
    if payload.target_agent_index >= len(payload.agents):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"target_agent_index {payload.target_agent_index} is past the end of "
            f"a {len(payload.agents)}-agent field",
        )

    cells = sweep_service.expand_grid(payload.parameters)
    if len(cells) > MAX_CELLS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"this grid has {len(cells)} cells, above the cap of {MAX_CELLS}. "
            "A search that wide deflates its own winner to nothing.",
        )

    agents: list[dict[str, Any]] = []
    for index, agent in enumerate(payload.agents):
        try:
            base_config = dict(agent.config)
            if index == payload.target_agent_index:
                # Validate against the first cell so a misspelled swept parameter fails now rather than on cell.
                build_agent("validation-probe", agent.agent_type, {**base_config, **cells[0]})
            resolved = build_agent("validation-probe", agent.agent_type, base_config).config
            latency = LatencyProfile.from_config(agent.latency).as_dict()
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        agents.append(
            {
                "id": f"agent-{index}",
                "name": agent.name or f"{agent.agent_type}-{index + 1}",
                "agent_type": str(agent.agent_type),
                "config": resolved,
                "latency": latency,
            }
        )

    row = Sweep(
        name=payload.name,
        grid_spec={
            "target_agent": f"agent-{payload.target_agent_index}",
            "parameters": payload.parameters,
            "path_count": payload.path_count,
            "steps": payload.steps,
            "base_seed": payload.base_seed,
            "in_sample_fraction": payload.in_sample_fraction,
            "cell_count": len(cells),
        },
        shared_config={
            "price_process": payload.price_process.model_dump(),
            "shocks": [s.model_dump() for s in payload.shocks],
            "agents": agents,
            "engine_config": engine_config_snapshot(settings),
        },
        status="QUEUED",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    background.add_task(execute_sweep, row.id, payload.workers)
    return row


@router.get("", response_model=list[SweepRead])
def list_sweeps(db: Session = Depends(get_db)):
    return list(db.scalars(select(Sweep).order_by(Sweep.created_at.desc())).all())


@router.get("/{sweep_id}", response_model=SweepDetail)
def get_sweep(sweep_id: str, db: Session = Depends(get_db)):
    row = _get(db, sweep_id)
    cells = list(
        db.scalars(select(SweepResult).where(SweepResult.sweep_id == row.id)).all()
    )
    return SweepDetail(
        **{c.name: getattr(row, c.name) for c in Sweep.__table__.columns},
        cells=[
            SweepCell(
                parameters=cell.parameters,
                in_sample_sharpe=cell.in_sample_sharpe,
                out_of_sample_sharpe=cell.out_of_sample_sharpe,
                deflated_sharpe=cell.deflated_sharpe,
                in_sample_pnl=cell.in_sample_pnl,
                out_of_sample_pnl=cell.out_of_sample_pnl,
            )
            for cell in cells
        ],
    )


@router.delete("/{sweep_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sweep(sweep_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, sweep_id))
    db.commit()


@router.get("/{sweep_id}/surface", response_model=SweepSurface)
def sweep_surface(
    sweep_id: str,
    x: str = Query(..., description="Parameter on the x axis"),
    y: str = Query(..., description="Parameter on the z axis"),
    metric: str = Query(
        "in_sample_sharpe",
        description="in_sample_sharpe | out_of_sample_sharpe | deflated_sharpe",
    ),
    db: Session = Depends(get_db),
):
    """A two-parameter slice, packed for the 3D landscape."""
    row = _get(db, sweep_id)
    if row.status != "COMPLETED":
        raise HTTPException(status.HTTP_409_CONFLICT, f"sweep is {row.status.lower()}")

    allowed = {"in_sample_sharpe", "out_of_sample_sharpe", "deflated_sharpe"}
    if metric not in allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"metric must be one of {sorted(allowed)}",
        )

    swept = set(row.grid_spec.get("parameters", {}))
    missing = {x, y} - swept
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{sorted(missing)} was not swept; this grid varied {sorted(swept)}",
        )

    cells = [
        {
            "parameters": cell.parameters,
            "in_sample_sharpe": cell.in_sample_sharpe,
            "out_of_sample_sharpe": cell.out_of_sample_sharpe,
            "deflated_sharpe": cell.deflated_sharpe,
        }
        for cell in db.scalars(
            select(SweepResult).where(SweepResult.sweep_id == row.id)
        ).all()
    ]
    packed = sweep_service.surface(cells, x, y, metric)
    return SweepSurface(
        sweep_id=row.id,
        pbo=row.pbo,
        best_cell=row.best_cell,
        robust_centroid=row.robust_centroid,
        **packed,
    )
