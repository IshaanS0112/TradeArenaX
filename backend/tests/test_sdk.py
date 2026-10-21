"""The library front door, and the separation that makes it possible."""

from __future__ import annotations

import subprocess
import sys

import pytest

from app.enums import AgentType
from app.sdk import Desk, Run, Shock, simulate


# ------------------------------------------------------------------ the seam
def test_importing_the_sdk_does_not_drag_in_the_database():
    """The claim the architecture rests on, checked rather than asserted.

    Importing the reporting helpers used to construct a SQLAlchemy engine at
    import time, which meant a notebook needed a Postgres driver to run a
    simulation that never touches a database. Run in a subprocess because this
    one cannot be observed once the test session has already imported the ORM.
    """
    code = (
        "import sys, app.sdk;"
        "banned = ('app.models', 'app.db', 'sqlalchemy', 'psycopg2');"
        "loaded = sorted(m for m in sys.modules if m.startswith(banned));"
        "print('|'.join(loaded))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "", f"app.sdk pulled in persistence: {out.stdout}"


# ----------------------------------------------------------------- the desks
def test_a_desk_routes_latency_keys_away_from_the_strategy():
    desk = Desk("flow", AgentType.NOISE_TRADER, activity=0.5, latency_out_us=900)

    assert desk.config == {"activity": 0.5}
    assert desk.latency == {"latency_out_us": 900}


def test_explicit_dicts_and_keywords_merge():
    desk = Desk("mm", "MARKET_MAKER", config={"spread": 0.2}, latency_in_us=100, quote_size=5)

    assert desk.config == {"spread": 0.2, "quote_size": 5}
    assert desk.latency == {"latency_in_us": 100}


def test_an_unknown_strategy_key_is_still_rejected():
    """The SDK is a front door, not a way around validation."""
    with pytest.raises(ValueError, match="unknown config keys"):
        simulate(steps=10, agents=[Desk("mm", "MARKET_MAKER", spreadd=0.2)])


# ------------------------------------------------------------------- the run
def test_a_default_run_makes_a_market():
    run = simulate(steps=300, seed=42)

    assert isinstance(run, Run)
    assert run.summary["total_trades"] > 0
    assert len(run.steps()) == 300


def test_the_conservation_invariant_holds_through_the_sdk():
    run = simulate(steps=400, seed=7, shocks=[Shock(step=200, pct=-8.0)])

    assert abs(run.conservation) < 1e-6
    assert run.conservation == run.summary["pnl_conservation_residual"]


def test_the_same_seed_gives_the_same_run():
    a = simulate(steps=200, seed=11)
    b = simulate(steps=200, seed=11)

    assert a.agents() == b.agents()


def test_a_different_seed_gives_a_different_run():
    a = simulate(steps=200, seed=11)
    b = simulate(steps=200, seed=12)

    assert a.agents() != b.agents()


def test_a_shock_moves_the_price_and_is_marked_on_the_step_it_fires():
    run = simulate(steps=300, seed=3, shocks=[Shock(step=150, pct=-10.0)])
    fired = [s["step"] for s in run.steps() if s["shock_fired"]]

    assert fired == [150]


# --------------------------------------------------------- the measurements
def test_agents_rows_carry_their_own_id():
    run = simulate(
        steps=300,
        seed=5,
        agents=[Desk("mm", "MARKET_MAKER"), Desk("flow", "NOISE_TRADER", activity=0.5)],
    )
    ids = {row["agent_id"] for row in run.agents()}

    assert ids == {"mm", "flow"}


def test_spread_decomposition_is_reachable_and_adds_up():
    run = simulate(
        steps=500,
        seed=9,
        agents=[Desk("mm", "MARKET_MAKER"), Desk("flow", "NOISE_TRADER", activity=0.6)],
    )
    rows = run.spread()

    assert rows, "a run with trades must decompose into something"
    for row in rows:
        assert row["effective_half_spread"] == pytest.approx(
            row["realised_half_spread"] + row["price_impact"], abs=1e-9
        )


def test_a_slow_maker_loses_races_to_faster_takers():
    """The headline behaviour of the event engine, reachable in three lines."""
    run = simulate(
        steps=600,
        seed=42,
        agents=[
            Desk("mm", "MARKET_MAKER", latency_out_us=1200, latency_jitter_us=300),
            Desk("fast", "NOISE_TRADER", activity=0.6, latency_out_us=400),
        ],
    )
    races = run.races()

    assert races, "a maker slower than its takers must lose some cancels"
    assert all(r["maker_agent_id"] == "mm" for r in races)
    assert all(r["margin_us"] > 0 for r in races), "a race is only a race if the fill won"


def test_zero_latency_records_no_races():
    run = simulate(steps=400, seed=42)

    assert run.races() == []


def test_greeks_are_empty_without_an_options_desk():
    assert simulate(steps=200, seed=1).greeks() == {}


def test_an_options_desk_reports_its_attribution():
    run = simulate(
        steps=400,
        seed=4,
        agents=[
            Desk("mm", "MARKET_MAKER"),
            Desk("opt", "OPTIONS_MAKER", client_intensity=0.3, hedge_band=20.0),
        ],
    )
    attribution = run.greeks()

    assert set(attribution) == {"opt"}
    assert "gamma_pnl" in attribution["opt"]
