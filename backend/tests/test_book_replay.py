"""Book reconstruction tests.

The claim being checked is that the persisted order stream is a *complete* event
log - submission step, priority sequence, and cancel step - so replaying it
reproduces the exact book the live engine had. If it did not, every book snapshot
the API serves after a run would be fiction.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.enums import AgentType
from app.models import Agent as AgentRow
from app.models import Simulation
from app.services import run_service
from app.services.book_replay import replay_book


def _seed_simulation(db, steps: int = 120, seed: int = 42) -> Simulation:
    sim = Simulation(
        name="replay",
        price_process_config={
            "initial_price": 100.0,
            "drift": 0.0,
            "volatility": 0.30,
            "random_seed": seed,
        },
        volatility_shock_config={"shocks": []},
    )
    db.add(sim)
    db.flush()
    for name, kind in (
        ("mm", AgentType.MARKET_MAKER),
        ("mom", AgentType.MOMENTUM),
        ("rev", AgentType.MEAN_REVERSION),
    ):
        db.add(
            AgentRow(
                simulation_id=sim.id,
                name=name,
                agent_type=str(kind),
                config=run_service.resolve_agent_config(kind, {}),
            )
        )
    db.commit()
    return sim


@pytest.fixture
def db(client):  # client builds the schema
    from app.db.session import SessionLocal

    session = SessionLocal()
    yield session
    session.close()


def test_replay_reproduces_the_final_book_exactly(db):
    """Compared against the live in-memory book, not against another replay."""
    sim = _seed_simulation(db, steps=150)
    settings = get_settings()
    result, _ = run_service.run_simulation(db, sim, steps=150, settings=settings)

    live = result.book.snapshot(levels=50)
    replayed = replay_book(db, sim.id, tick_size=settings.tick_size).book.snapshot(levels=50)

    assert replayed["best_bid"] == live["best_bid"]
    assert replayed["best_ask"] == live["best_ask"]
    assert replayed["mid_price"] == live["mid_price"]
    assert replayed["bids"] == live["bids"]
    assert replayed["asks"] == live["asks"]
    assert replayed["total_trades"] == live["total_trades"]


def test_replay_reproduces_the_trade_count(db):
    sim = _seed_simulation(db, steps=150)
    settings = get_settings()
    result, _ = run_service.run_simulation(db, sim, steps=150, settings=settings)

    replay = replay_book(db, sim.id, tick_size=settings.tick_size)
    assert replay.trades_replayed == len(result.fills), (
        "a replay that produces a different number of executions is not a replay"
    )


def test_replay_at_an_intermediate_step_is_a_prefix_of_the_run(db):
    sim = _seed_simulation(db, steps=150)
    settings = get_settings()
    run_service.run_simulation(db, sim, steps=150, settings=settings)

    early = replay_book(db, sim.id, tick_size=settings.tick_size, up_to_step=50)
    late = replay_book(db, sim.id, tick_size=settings.tick_size, up_to_step=120)

    assert early.step == 50 and late.step == 120
    assert early.orders_replayed < late.orders_replayed
    assert early.trades_replayed <= late.trades_replayed


def test_cancelled_quotes_do_not_pile_up_in_a_replay(db):
    """Without the cancel step in the log, every quote ever posted would rest."""
    sim = _seed_simulation(db, steps=150)
    settings = get_settings()
    run_service.run_simulation(db, sim, steps=150, settings=settings)

    replay = replay_book(db, sim.id, tick_size=settings.tick_size, up_to_step=140)
    snapshot = replay.book.snapshot(levels=50)

    assert len(snapshot["bids"]) < 20, (
        f"{len(snapshot['bids'])} bid levels resting after 140 steps means the "
        "maker's cancels were lost in persistence"
    )


def test_replaying_an_unknown_simulation_gives_an_empty_book(db):
    replay = replay_book(db, "00000000-0000-0000-0000-000000000000", tick_size=0.01)
    assert replay.orders_replayed == 0
    assert replay.book.best_bid is None


def test_rerunning_a_simulation_replaces_its_results_rather_than_appending(db):
    """Two step-1 rows would make every chart zigzag back in time."""
    from app.models import AgentPerformance, Trade

    sim = _seed_simulation(db, steps=60)
    settings = get_settings()
    run_service.run_simulation(db, sim, steps=60, settings=settings)
    trades_first = run_service.count_rows(db, Trade, sim.id)
    perf_first = run_service.count_rows(db, AgentPerformance, sim.id)

    run_service.run_simulation(db, sim, steps=60, settings=settings)
    assert run_service.count_rows(db, Trade, sim.id) == trades_first
    assert run_service.count_rows(db, AgentPerformance, sim.id) == perf_first
