"""Shared fixtures.

The API tests run against SQLite in a temp file rather than Postgres, so a clone
with no database container can still execute the whole suite. The two dialect
differences that matter (JSONB, UUID) are handled by the column variants in
db/session.py, so the code under test is the same code that runs in production.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_tmpdir = tempfile.mkdtemp(prefix="tradearenax-tests-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{Path(_tmpdir) / 'test.db'}")

from app.config import get_settings  # noqa: E402
from app.enums import AgentType, SelfTradePrevention  # noqa: E402
from app.services.order_book import OrderBook  # noqa: E402
from app.services.price_process import PriceProcess  # noqa: E402
from app.services.simulation_engine import SimulationEngine  # noqa: E402


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def book():
    """A book on a 1-cent tick with the default self-trade policy."""
    return OrderBook(tick_size=0.01, self_trade_prevention=SelfTradePrevention.CANCEL_RESTING)


@pytest.fixture
def price_process(settings):
    return PriceProcess(
        initial_price=100.0, drift=0.0, volatility=0.30, dt=settings.dt, seed=42
    )


@pytest.fixture
def engine(price_process, settings):
    return SimulationEngine(price_process=price_process, settings=settings)


@pytest.fixture
def three_agent_engine(engine):
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    engine.add_agent("rev", AgentType.MEAN_REVERSION, {})
    return engine


@pytest.fixture
def client():
    """FastAPI test client on a fresh schema."""
    from fastapi.testclient import TestClient

    import app.models  # noqa: F401
    from app.db.session import Base, engine as db_engine
    from app.main import app as fastapi_app

    Base.metadata.drop_all(bind=db_engine)
    Base.metadata.create_all(bind=db_engine)
    with TestClient(fastapi_app) as c:
        yield c
    Base.metadata.drop_all(bind=db_engine)
