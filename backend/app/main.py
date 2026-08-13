"""TradeArena X API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db.session import Base, engine
from app.routers import simulations

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("tradearenax")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # create_all is adequate here because the schema is append-only for V1. A
    # migration tool (Alembic) is the correct answer the moment a column has to
    # change shape - noted in docs/architecture.md.
    import app.models  # noqa: F401  (register mappers before create_all)

    Base.metadata.create_all(bind=engine)
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
app.include_router(simulations.meta_router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
