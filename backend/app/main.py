"""TradeArena X API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import ensembles, simulations, sweeps


def _run_migrations() -> None:
    """Bring the database up to head, or fail loudly."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    settings = get_settings()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)

    # A database created by V1 has the tables but no alembic_version row.
    inspector = inspect(create_engine(settings.database_url))
    tables = set(inspector.get_table_names())
    if "simulations" in tables and "alembic_version" not in tables:
        # Which revision it is already at is decided by a table that only.
        already_current = "ensembles" in tables
        stamp_at = "head" if already_current else BASELINE_REVISION
        logger.info("un-stamped database detected; stamping %s before upgrading", stamp_at)
        command.stamp(config, stamp_at)

    command.upgrade(config, "head")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("tradearenax")

# : The revision that reproduces the pre-migration (V1) schema exactly.
BASELINE_REVISION = "155be5af5848"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Alembic, not create_all.
    import app.models  # noqa: F401  (register mappers before either path)

    _run_migrations()
    settings = get_settings()
    logger.info(
        "TradeArena X up. tick=%s capital_base=%s steps_per_year=%s "
        "maker_fee_bps=%s taker_fee_bps=%s",
        settings.tick_size,
        settings.capital_base,
        settings.steps_per_year,
        settings.maker_fee_bps,
        settings.taker_fee_bps,
    )
    yield


settings = get_settings()

app = FastAPI(
    title="TradeArena X API",
    version="1.0.0",
    description=(
        "Market-making and trading strategy simulation. A price-time-priority "
        "limit order book, three agent archetypes (inventory-aware market making, "
        "momentum, mean reversion) trading against each other over a synthetic GBM "
        "price path with configurable volatility shocks, and a comparison layer "
        "reporting Sharpe, max drawdown, win rate and inventory risk.\n\n"
        "This is a strategy research and education tool. The price process is "
        "synthetic; there is no live market data, no broker connection, and no "
        "capital at risk. See GET /meta/engine-config for every formula in force."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(simulations.router)
app.include_router(ensembles.router)
app.include_router(sweeps.router)
app.include_router(simulations.meta_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
