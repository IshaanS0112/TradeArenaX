"""The event queue, and the latency it exists to model."""

from __future__ import annotations

import pytest

from app.enums import AgentType, Side
from app.services.event_engine import (
    AGENT_WAKE,
    CANCEL_ARRIVAL,
    ORDER_ARRIVAL,
    EventDrivenEngine,
)
from app.services.latency import STEP_DURATION_US, LatencyProfile
from app.services.price_process import PriceProcess, VolatilityShock
from app.services.simulation_engine import SimulationEngine

AGENTS = (
    ("mm", AgentType.MARKET_MAKER, {}),
    ("mom", AgentType.MOMENTUM, {}),
    ("rev", AgentType.MEAN_REVERSION, {}),
)


def _process(settings, seed=42, shock=False):
    shocks = (
        (
            VolatilityShock(
                step=120, magnitude_pct=-8.0, vol_multiplier=3.0, vol_half_life_steps=40
            ),
        )
        if shock
        else ()
    )
    return PriceProcess(
        initial_price=100.0,
        drift=0.0,
        volatility=0.30,
        dt=settings.dt,
        seed=seed,
        shocks=shocks,
    )


def _step_engine(settings, seed=42, shock=False, agents=AGENTS):
    engine = SimulationEngine(price_process=_process(settings, seed, shock), settings=settings)
    for agent_id, agent_type, config in agents:
        engine.add_agent(agent_id, agent_type, config)
    return engine


def _event_engine(settings, seed=42, shock=False, agents=AGENTS, latency=None):
    engine = EventDrivenEngine(
        price_process=_process(settings, seed, shock), settings=settings
    )
    for agent_id, agent_type, config in agents:
        engine.add_agent(
            agent_id, agent_type, config, latency=(latency or {}).get(agent_id)
        )
    return engine


def _fingerprint(result) -> dict:
    """Everything a reader of this simulation would ever look."""
    return {
        "steps": [
            (
                r.step,
                round(r.reference_price, 12),
                None if r.mid_price is None else round(r.mid_price, 12),
                round(r.mark_price, 12),
                None if r.best_bid is None else round(r.best_bid, 12),
                None if r.best_ask is None else round(r.best_ask, 12),
                r.trade_count,
                r.shock_fired,
            )
            for r in result.step_records
        ],
        "fills": [
            (f.step, f.price_ticks, f.quantity, f.buy_agent_id, f.sell_agent_id, str(f.aggressor_side))
            for f in result.fills
        ],
        "orders": [
            (o.step, o.agent_id, str(o.side), o.price_ticks, o.quantity, str(o.status))
            for o in result.orders
        ],
        "pnl": {
            agent_id: [round(v, 12) for v in state.pnl_series]
            for agent_id, state in result.agent_states.items()
        },
        "inventory": {
            agent_id: [round(v, 12) for v in state.inventory_series]
            for agent_id, state in result.agent_states.items()
        },
    }


def test_zero_latency_reproduces_the_step_engine_exactly(settings):
    """The regression guard for the entire rewrite."""
    step_result = _step_engine(settings).run(300)
    event_engine = _event_engine(settings)
    assert event_engine.all_latencies_zero
    event_result = event_engine.run(300)

    assert _fingerprint(event_result) == _fingerprint(step_result)


def test_zero_latency_equivalence_holds_through_a_shock(settings):
    """The interesting path is the one with a discontinuity."""
    step_result = _step_engine(settings, shock=True).run(240)
    event_result = _event_engine(settings, shock=True).run(240)
    assert _fingerprint(event_result) == _fingerprint(step_result)


def test_event_queue_is_deterministic(settings):
    """Same seed, same event ordering, byte-identical output."""
    latency = {
        "mm": {"latency_in_us": 400, "latency_out_us": 900, "latency_jitter_us": 120.0},
        "mom": {"latency_in_us": 100, "latency_out_us": 150, "latency_jitter_us": 40.0},
    }
    first = _event_engine(settings, latency=latency)
    second = _event_engine(settings, latency=latency)
    a, b = first.run(200), second.run(200)

    assert _fingerprint(a) == _fingerprint(b)
    assert [(e.timestamp_us, e.sequence, e.kind) for e in first.events] == [
        (e.timestamp_us, e.sequence, e.kind) for e in second.events
    ]


def test_orders_arrive_after_the_decision_that_produced_them(settings):
    """Nothing is instantaneous once latency is configured."""
    engine = _event_engine(
        settings,
        latency={"mm": {"latency_in_us": 500, "latency_out_us": 2_000}},
    )
    engine.run(60)

    wakes = {
        (e.step, e.agent_id): e.timestamp_us for e in engine.events if e.kind == AGENT_WAKE
    }
    arrivals = [e for e in engine.events if e.kind == ORDER_ARRIVAL and e.agent_id == "mm"]
    assert arrivals, "the maker should have sent orders"
    for arrival in arrivals:
        assert arrival.timestamp_us >= wakes[(arrival.step, "mm")] + 2_000
        assert arrival.payload["flight_us"] >= 2_000


def test_a_stale_feed_makes_the_agent_act_on_an_out_of_date_world(settings):
    """latency_in is staleness measured against the clock, not against a label."""
    engine = _event_engine(
        settings,
        latency={"mm": {"latency_in_us": int(2.5 * STEP_DURATION_US)}},
    )
    engine.run(40)

    wakes = [e for e in engine.events if e.kind == AGENT_WAKE and e.agent_id == "mm"]
    assert wakes, "the maker should have woken"
    for wake in wakes:
        wall_clock_step = wake.timestamp_us // STEP_DURATION_US + 1
        assert wake.payload["observed_step"] == wake.step
        assert wall_clock_step > wake.payload["observed_step"], (
            "a feed 2.5 steps behind must have the world move on before the "
            "agent acts on it"
        )


def test_cancel_in_flight_loses_the_race_and_the_fill_happens(settings):
    """The scenario the latency model exists."""
    engine = _event_engine(
        settings,
        shock=True,
        latency={
            "mm": {"latency_out_us": 250_000},
            "mom": {"latency_out_us": 1_000},
            "rev": {"latency_out_us": 1_000},
        },
    )
    engine.run(260)

    assert engine.races, "a slow maker against fast takers must lose at least one race"
    race = engine.races[0]
    assert race.maker_agent_id == "mm"
    assert race.cancel_issued_us <= race.fill_us
    assert race.cancel_arrival_us > race.fill_us, "the cancel must have been too late"
    assert race.margin_us > 0


def test_adverse_fills_rise_monotonically_with_the_latency_gap(settings):
    """The finding is the relationship, not the absolute numbers."""
    counts = []
    for gap_us in (0, 50_000, 150_000, 400_000):
        engine = _event_engine(
            settings,
            shock=True,
            latency={
                "mm": {"latency_out_us": gap_us},
                "mom": {"latency_out_us": 0},
                "rev": {"latency_out_us": 0},
            },
        )
        engine.run(260)
        counts.append(len(engine.races))

    assert counts[0] == 0, "a maker as fast as its takers is never caught in flight"
    assert counts == sorted(counts), f"adverse fills should not fall with the gap: {counts}"
    assert counts[-1] > counts[0]


def test_latency_config_rejects_unknown_keys():
    """A misspelled key is a 422, not a silent default."""
    with pytest.raises(ValueError, match="unknown latency config key"):
        LatencyProfile.from_config({"latency_our_us": 100})
    with pytest.raises(ValueError, match=">= 0"):
        LatencyProfile.from_config({"latency_out_us": -5})


def test_jitter_is_clamped_at_zero_and_deterministic(settings):
    """Negative latency would be a machine that answers before it is asked."""
    import numpy as np

    profile = LatencyProfile(latency_out_us=10, latency_jitter_us=500.0)
    rng = np.random.default_rng(7)
    draws = [profile.sample_out(rng) for _ in range(2_000)]
    assert min(draws) >= 0
    assert max(draws) > 10, "jitter should actually vary the delay"

    repeat = np.random.default_rng(7)
    assert draws == [profile.sample_out(repeat) for _ in range(2_000)]


def test_pnl_conservation_holds_under_latency(settings):
    """The V1 invariant, extended to the new engine."""
    engine = _event_engine(
        settings,
        shock=True,
        latency={
            "mm": {"latency_in_us": 300, "latency_out_us": 120_000, "latency_jitter_us": 25.0},
            "mom": {"latency_out_us": 3_000},
        },
    )
    result = engine.run(300)

    total = sum(
        state.tracker.realized_pnl + state.tracker.unrealized_pnl(engine._mark_price())
        for state in result.agent_states.values()
    )
    fees = sum(state.tracker.fees_paid for state in result.agent_states.values())
    assert abs(total + fees) < 1e-6, f"PnL conservation broken: {total + fees}"


def test_every_cancel_arrival_follows_its_wake(settings):
    """Cancels are events on the same wire as orders, not instant side effects."""
    engine = _event_engine(settings, latency={"mm": {"latency_out_us": 5_000}})
    engine.run(80)

    cancels = [e for e in engine.events if e.kind == CANCEL_ARRIVAL]
    assert cancels, "the maker reconciles its quotes, so some must be cancelled"
    for cancel in cancels:
        assert cancel.payload["flight_us"] >= 5_000


def test_book_is_never_crossed_across_agents_under_latency(settings):
    """Delay must not let executable liquidity sit unmatched."""
    engine = _event_engine(
        settings,
        latency={"mm": {"latency_out_us": 90_000}, "mom": {"latency_out_us": 500}},
    )
    engine.run(200)
    engine.book.assert_invariants()

    bid, ask = engine.book.best_bid_ticks(), engine.book.best_ask_ticks()
    if bid is not None and ask is not None:
        crossing_agents = {
            o.agent_id
            for o in engine.book.open_orders()
            if (o.side is Side.BUY and o.price_ticks >= ask)
            or (o.side is Side.SELL and o.price_ticks <= bid)
        }
        assert len(crossing_agents) <= 1


def test_the_same_configuration_reproduces_across_processes(settings):
    """Reproducibility has to survive a new interpreter, not just a new object."""
    import subprocess
    import sys
    from pathlib import Path

    script = (
        "from app.config import get_settings;"
        "from app.enums import AgentType;"
        "from app.services.event_engine import EventDrivenEngine;"
        "from app.services.price_process import PriceProcess;"
        "s=get_settings();"
        "p=PriceProcess(initial_price=100.0, drift=0.0, volatility=0.3, dt=s.dt, seed=42);"
        "e=EventDrivenEngine(price_process=p, settings=s);"
        "e.add_agent('mm', AgentType.MARKET_MAKER, {});"
        "e.add_agent('noise', AgentType.NOISE_TRADER, {'activity': 0.6});"
        "r=e.run(80);"
        "print(len(r.fills), round(r.summaries['mm'].total_pnl, 9))"
    )
    root = Path(__file__).resolve().parents[1]
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            capture_output=True,
            text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin", "DATABASE_URL": "sqlite://"},
            check=True,
        ).stdout.strip()
        for seed in ("0", "1", "12345")
    }
    assert len(outputs) == 1, f"the run differs with the hash seed: {outputs}"
