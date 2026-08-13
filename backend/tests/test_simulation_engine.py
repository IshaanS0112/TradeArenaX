"""Whole-simulation tests.

The headline test is PnL conservation: nothing enters or leaves a closed market
except fees, so the agents' total PnL must sum to minus the fees collected. It is
the strongest single check on the accounting layer, because almost any error in
fill application, lot matching, or mark-to-market breaks it.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.enums import AgentType, Side
from app.services.price_process import PriceProcess, VolatilityShock
from app.services.simulation_engine import SimulationEngine


def make_engine(settings: Settings | None = None, seed: int = 42, **process_kwargs):
    settings = settings or Settings()
    defaults = dict(initial_price=100.0, drift=0.0, volatility=0.30, dt=settings.dt)
    defaults.update(process_kwargs)
    process = PriceProcess(seed=seed, **defaults)
    engine = SimulationEngine(price_process=process, settings=settings)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    engine.add_agent("rev", AgentType.MEAN_REVERSION, {})
    return engine


def test_a_run_produces_trades():
    result = make_engine().run(400)
    assert len(result.fills) > 0, (
        "no trades means the agents never interacted and every metric is vacuous"
    )
    assert "No trades occurred" not in " ".join(result.warnings)


def test_pnl_is_conserved_with_no_fees():
    result = make_engine().run(500)
    total = sum(s.total_pnl for s in result.summaries.values())
    assert total == pytest.approx(0.0, abs=1e-6), (
        "every trade moves value between two participants; with no fees the sum "
        "of all PnL must be exactly zero"
    )


def test_pnl_is_conserved_with_fees_and_the_shortfall_equals_the_fees():
    settings = Settings(maker_fee_bps=1.0, taker_fee_bps=3.0)
    result = make_engine(settings).run(500)

    total = sum(s.total_pnl for s in result.summaries.values())
    fees = sum(s.fees_paid for s in result.summaries.values())
    assert fees > 0, "precondition: fees were actually charged"
    assert total == pytest.approx(-fees, abs=1e-6)


def test_inventory_is_conserved_across_agents():
    """Every share bought was sold by somebody."""
    result = make_engine().run(400)
    net = sum(s.final_inventory for s in result.summaries.values())
    assert net == pytest.approx(0.0, abs=1e-9)


def test_the_book_ends_uncrossed_and_internally_consistent():
    engine = make_engine()
    engine.run(400)
    engine.book.assert_invariants()


def test_a_run_is_reproducible():
    a = make_engine(seed=99).run(300)
    b = make_engine(seed=99).run(300)

    assert a.reference_path == b.reference_path
    assert len(a.fills) == len(b.fills)
    for agent_id in a.summaries:
        assert a.summaries[agent_id].total_pnl == pytest.approx(
            b.summaries[agent_id].total_pnl
        ), "the same seed and config must produce the same result, or nothing is measurable"


def test_different_seeds_produce_different_outcomes():
    a = make_engine(seed=1).run(300)
    b = make_engine(seed=2).run(300)
    assert a.reference_path != b.reference_path


def test_the_maker_earns_the_spread_in_a_calm_market():
    """The economic sanity check on the whole model.

    A market maker quoting a fixed width in a low-volatility market with no
    persistent trend should collect the spread. If it cannot make money here, the
    quoting or the accounting is wrong regardless of what the tests say.
    """
    result = make_engine(seed=5, volatility=0.12).run(1000)
    mm = result.summaries["mm"]
    assert mm.total_pnl > 0, f"maker lost money in a calm market: {mm.total_pnl:.2f}"
    assert mm.fill_count > 50, "the maker has to actually be getting hit"


def test_series_lengths_match_the_step_count():
    result = make_engine().run(250)
    assert len(result.step_records) == 250
    assert len(result.reference_path) == 251, "path includes the initial price"
    for state in result.agent_states.values():
        assert len(state.pnl_series) == 250
        assert len(state.inventory_series) == 250
        assert len(state.realized_series) == 250


def test_total_pnl_series_equals_realized_plus_unrealized_at_every_step():
    result = make_engine().run(200)
    for state in result.agent_states.values():
        for total, realized, unrealized in zip(
            state.pnl_series, state.realized_series, state.unrealized_series
        ):
            assert total == pytest.approx(realized + unrealized)


# --------------------------------------------------------------- shock effects
def test_a_shock_moves_the_reference_price_and_is_recorded():
    settings = Settings()
    process = PriceProcess(
        initial_price=100.0,
        drift=0.0,
        volatility=0.20,
        dt=settings.dt,
        seed=42,
        shocks=(VolatilityShock(step=200, magnitude_pct=-10.0, vol_multiplier=3.0),),
    )
    engine = SimulationEngine(price_process=process, settings=settings)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    result = engine.run(400)

    shock_record = next(r for r in result.step_records if r.step == 200)
    assert shock_record.shock_fired is True

    before = result.step_records[198].reference_price
    after = shock_record.reference_price
    assert after < before * 0.95, "a -10% jump has to be visible in the path"
    assert shock_record.volatility > result.step_records[198].volatility


def test_a_shock_run_still_conserves_pnl():
    settings = Settings()
    process = PriceProcess(
        initial_price=100.0,
        drift=0.0,
        volatility=0.25,
        dt=settings.dt,
        seed=13,
        shocks=(VolatilityShock(step=150, magnitude_pct=-15.0, vol_multiplier=4.0),),
    )
    engine = SimulationEngine(price_process=process, settings=settings)
    for name, kind in (
        ("mm", AgentType.MARKET_MAKER),
        ("mom", AgentType.MOMENTUM),
        ("rev", AgentType.MEAN_REVERSION),
    ):
        engine.add_agent(name, kind, {})
    result = engine.run(400)

    total = sum(s.total_pnl for s in result.summaries.values())
    assert total == pytest.approx(0.0, abs=1e-6), (
        "a discontinuity in the price path must not break the accounting"
    )


# ------------------------------------------------------------- risk enforcement
# Risk is enforced in two layers, and they are tested separately because they are
# not equally likely to run:
#
#   1. Pre-trade clamping. An agent shrinks any order that would breach its own
#      limit. This is the layer that actually operates - see the test below - and
#      it means a well-behaved agent never breaches at all.
#   2. Post-fill liquidation. A net for the cases layer 1 cannot cover: a resting
#      order that fills several steps after it was sized, or an agent
#      implementation with a sizing bug. It is defence in depth, so it is proven
#      by constructing a breach directly rather than by hoping a random path
#      produces one.


def test_pre_trade_clamping_keeps_inventory_inside_every_limit():
    """Layer 1: a position limit checked only after the fact is not a limit."""
    result = make_engine(seed=8, drift=1.5, volatility=0.60).run(600)
    for agent_id, state in result.agent_states.items():
        limit = state.agent.max_inventory
        worst = max(abs(i) for i in state.inventory_series)
        assert worst <= limit + 1e-9, (
            f"{agent_id} reached {worst} against a limit of {limit}"
        )


def test_forced_liquidation_reduces_a_breached_position():
    """Layer 2, driven directly: inject a breach and check the net catches it."""
    from app.enums import Side

    settings = Settings()
    engine = SimulationEngine(
        price_process=PriceProcess(
            initial_price=100.0, drift=0.0, volatility=0.20, dt=settings.dt, seed=4
        ),
        settings=settings,
    )
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {"max_inventory": 12.0})

    # Resting liquidity for the liquidation to hit.
    engine.book.submit("mm", Side.BUY, 100.0, 99.50, step=1)

    breached = engine.states["mom"]
    breached.tracker.apply_fill(Side.BUY, 100.0, 100.0, is_maker=False, step=1)
    assert breached.tracker.inventory_risk_score(12.0) > 1.0

    fills = engine._enforce_inventory_limits(step=2)

    assert fills, "a breach with liquidity available must produce a liquidation trade"
    assert breached.tracker.inventory == pytest.approx(50.0), (
        "liquidation_fraction of 0.5 sells half of a 100-share position"
    )
    assert breached.liquidation_events == [2]
    assert any("forced liquidation" in f for f in breached.agent.flags)


def test_liquidation_uses_market_orders_not_resting_limits():
    """A limit order that sits in the book is hope, not risk reduction."""
    from app.enums import OrderType, Side

    settings = Settings()
    engine = SimulationEngine(
        price_process=PriceProcess(
            initial_price=100.0, drift=0.0, volatility=0.20, dt=settings.dt, seed=4
        ),
        settings=settings,
    )
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {"max_inventory": 10.0})
    engine.book.submit("mm", Side.BUY, 100.0, 99.50, step=1)
    engine.states["mom"].tracker.apply_fill(Side.BUY, 80.0, 100.0, is_maker=False, step=1)

    engine._enforce_inventory_limits(step=2)
    liquidation_orders = [
        o for o in engine.book.all_orders if o.agent_id == "mom"
    ]
    assert liquidation_orders
    assert all(o.order_type is OrderType.MARKET for o in liquidation_orders)


def test_liquidation_into_an_empty_book_warns_instead_of_failing_silently():
    settings = Settings()
    engine = SimulationEngine(
        price_process=PriceProcess(
            initial_price=100.0, drift=0.0, volatility=0.20, dt=settings.dt, seed=4
        ),
        settings=settings,
    )
    engine.add_agent("mom", AgentType.MOMENTUM, {"max_inventory": 10.0})
    engine.states["mom"].tracker.apply_fill(Side.BUY, 80.0, 100.0, is_maker=False, step=1)

    fills = engine._enforce_inventory_limits(step=2)

    assert fills == []
    assert any("could not liquidate" in w for w in engine.warnings), (
        "an unfillable liquidation must be reported, not swallowed"
    )
    assert engine.states["mom"].tracker.inventory == pytest.approx(80.0)


# ------------------------------------------------------- quote reconciliation
def test_quotes_survive_steps_instead_of_being_cancelled_and_reposted():
    """Cancel-and-repost throws away queue position every single step."""
    result = make_engine(seed=21, volatility=0.05).run(400)
    mm_state = result.agent_states["mm"]
    assert mm_state.quotes_kept > 0, (
        "at low volatility the maker's desired price is often unchanged, and an "
        "unchanged quote must keep its place in the queue"
    )


def test_the_book_holds_liquidity_between_steps():
    """Takers must be able to find a resting quote when they act.

    The first version of this engine pulled all maker quotes at the top of each
    step, so a taker that submitted before the maker re-quoted hit an empty book
    and never traded.
    """
    result = make_engine(seed=3).run(300)
    assert result.steps_with_no_two_sided_book < 300 * 0.5


# -------------------------------------------------------------- guard rails
def test_running_with_no_agents_is_rejected():
    settings = Settings()
    engine = SimulationEngine(
        price_process=PriceProcess(
            initial_price=100.0, drift=0.0, volatility=0.2, dt=settings.dt, seed=1
        ),
        settings=settings,
    )
    with pytest.raises(ValueError, match="no agents"):
        engine.run(10)


def test_a_run_without_a_maker_is_warned_about():
    settings = Settings()
    engine = SimulationEngine(
        price_process=PriceProcess(
            initial_price=100.0, drift=0.0, volatility=0.2, dt=settings.dt, seed=1
        ),
        settings=settings,
    )
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    result = engine.run(50)
    assert any("No liquidity-providing agent" in w for w in result.warnings)


def test_step_count_is_bounded():
    engine = make_engine(Settings(max_simulation_steps=100))
    with pytest.raises(ValueError, match="exceeds max_simulation_steps"):
        engine.run(101)


def test_non_positive_step_count_is_rejected():
    with pytest.raises(ValueError):
        make_engine().run(0)


def test_duplicate_agent_ids_are_rejected():
    engine = make_engine()
    with pytest.raises(ValueError, match="duplicate agent id"):
        engine.add_agent("mm", AgentType.MOMENTUM, {})


def test_agent_count_is_bounded():
    engine = make_engine(Settings(max_agents_per_simulation=3))
    with pytest.raises(ValueError, match="at most 3 agents"):
        engine.add_agent("extra", AgentType.MOMENTUM, {})
