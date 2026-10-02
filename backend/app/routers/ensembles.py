"""Ensemble endpoints: many paths, one answer with an error bar."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import SessionLocal, get_db
from app.models import Ensemble
from app.schemas import (
    EnsembleCreate,
    EnsembleDistribution,
    EnsemblePaths,
    EnsembleRead,
)
from app.services import ensembles as ensemble_service
from app.services.agents import build_agent
from app.services.latency import LatencyProfile
from app.services.run_service import engine_config_snapshot

logger = logging.getLogger("tradearenax.ensembles.api")
router = APIRouter(prefix="/ensembles", tags=["ensembles"])


def _get(db: Session, ensemble_id: str) -> Ensemble:
    row = db.get(Ensemble, ensemble_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ensemble not found")
    return row


def execute_ensemble(ensemble_id: str, workers: int | None = None) -> None:
    """Run every path and store the aggregates."""
    settings = get_settings()
    db = SessionLocal()
    try:
        row = db.get(Ensemble, ensemble_id)
        if row is None:  # pragma: no cover - deleted mid-flight
            return
        row.status = "RUNNING"
        db.commit()

        specs = ensemble_service.build_specs(
            shared_config=row.shared_config,
            base_seed=row.base_seed,
            path_count=row.path_count,
            steps=row.steps,
            settings=settings,
        )

        def progress(done: int) -> None:
            row.completed_paths = done
            db.commit()

        paths = ensemble_service.execute(specs, workers=workers, on_path_complete=progress)
        names = {a["id"]: a["name"] for a in row.shared_config.get("agents", [])}

        row.aggregates = ensemble_service.aggregate_paths(paths, names, settings)
        row.paths = ensemble_service.path_rows(paths, names)
        row.completed_paths = len(paths)
        row.status = "COMPLETED"
        db.commit()
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("ensemble %s failed", ensemble_id)
        db.rollback()
        row = db.get(Ensemble, ensemble_id)
        if row is not None:
            row.status = "FAILED"
            row.error = str(exc)[:500]
            db.commit()
    finally:
        db.close()


@router.post("", response_model=EnsembleRead, status_code=status.HTTP_202_ACCEPTED)
def create_ensemble(
    payload: EnsembleCreate,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Create an ensemble and start."""
    settings = get_settings()
    if not payload.agents:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "an ensemble needs at least one agent"
        )

    agents: list[dict[str, Any]] = []
    for index, agent in enumerate(payload.agents):
        try:
            resolved = build_agent("validation-probe", agent.agent_type, agent.config).config
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

    row = Ensemble(
        name=payload.name,
        base_seed=payload.base_seed,
        path_count=payload.path_count,
        steps=payload.steps,
        shared_config={
            "price_process": payload.price_process.model_dump(),
            "shocks": [s.model_dump() for s in payload.shocks],
            "agents": agents,
        },
        engine_config=engine_config_snapshot(settings),
        status="QUEUED",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    background.add_task(execute_ensemble, row.id, payload.workers)
    return row


@router.get("", response_model=list[EnsembleRead])
def list_ensembles(db: Session = Depends(get_db)):
    return list(db.scalars(select(Ensemble).order_by(Ensemble.created_at.desc())).all())


@router.get("/{ensemble_id}", response_model=EnsembleRead)
def get_ensemble(ensemble_id: str, db: Session = Depends(get_db)):
    return _get(db, ensemble_id)


@router.delete("/{ensemble_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ensemble(ensemble_id: str, db: Session = Depends(get_db)):
    db.delete(_get(db, ensemble_id))
    db.commit()


@router.get("/{ensemble_id}/distribution", response_model=EnsembleDistribution)
def distribution(ensemble_id: str, db: Session = Depends(get_db)):
    """Per-metric distributions, histograms and confidence intervals."""
    row = _get(db, ensemble_id)
    if row.status != "COMPLETED":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"ensemble is {row.status.lower()} - {row.completed_paths}/{row.path_count} "
            "paths finished",
        )
    return EnsembleDistribution(
        ensemble_id=row.id,
        path_count=row.path_count,
        steps=row.steps,
        agents=row.aggregates.get("agents", {}),
        market=row.aggregates.get("market", {}),
        max_pnl_conservation_residual=row.aggregates.get(
            "max_pnl_conservation_residual", 0.0
        ),
    )


@router.get("/{ensemble_id}/paths", response_model=EnsemblePaths)
def paths(ensemble_id: str, db: Session = Depends(get_db)):
    """One summary row per path - the table under the fan chart."""
    row = _get(db, ensemble_id)
    return EnsemblePaths(ensemble_id=row.id, status=row.status, paths=row.paths or [])
