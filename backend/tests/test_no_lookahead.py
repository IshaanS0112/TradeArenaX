"""Look-ahead bias tests.

Look-ahead is the failure mode that makes a backtest worthless while making it
look excellent, and it is invisible in ordinary output - the numbers just come
out good. So it gets tested directly rather than argued about in a README.

Two claims are checked:

1. The directional agents cannot see the latent GBM level. Perturbing
   ``reference_price`` by an arbitrary amount must not change a single decision
   they make. (The market maker legitimately uses it as its fair value, and is
   asserted to be sensitive to it, so the test cannot pass by accident.)

2. The observable price history a view carries never includes the step being
   decided. A signal that can read its own outcome predicts perfectly.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.enums import AgentType
from app.services.agents import build_agent
from app.services.agents.base import MarketView
from app.services.price_process import PriceProcess
from app.services.simulation_engine import SimulationEngine


def base_view(**overrides) -> MarketView:
    defaults = dict(
        step=100,
        best_bid=99.95,
        best_ask=100.05,
        mid_price=100.0,
        reference_price=100.0,
        price_history=tuple(100.0 + i * 0.05 for i in range(40)),
        trade_prices=(100.0, 100.1, 100.2),
        inventory=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        tick_size=0.01,
    )
    defaults.update(overrides)
    return MarketView(**defaults)


@pytest.mark.parametrize("agent_type", [AgentType.MOMENTUM, AgentType.MEAN_REVERSION])
def test_directional_agents_ignore_the_latent_reference_price(agent_type):
    honest = build_agent("a", agent_type, {"cooldown_steps": 0})
    cheating = build_agent("b", agent_type, {"cooldown_steps": 0})

    truthful = honest.decide(base_view(reference_price=100.0))
    # A reference price 50% away is information no participant could have.
    perturbed = cheating.decide(base_view(reference_price=150.0))

    assert [(i.side, i.quantity, i.order_type) for i in truthful] == [
        (i.side, i.quantity, i.order_type) for i in perturbed
    ], f"{agent_type} changed its decision when the latent price moved: look-ahead bias"


def test_the_market_maker_is_deliberately_sensitive_to_the_reference_price():
    """Guards the test above from passing because nothing reads the field at all."""
    agent = build_agent("mm", AgentType.MARKET_MAKER, {"volatility_widening": False})
    at_100 = agent.decide(base_view(reference_price=100.0))
    at_150 = agent.decide(base_view(reference_price=150.0))

    assert [i.price for i in at_100] != [i.price for i in at_150]


def test_the_price_history_in_a_view_excludes_the_current_step():
    """A view at step t may contain at most t-1 completed observations."""
    settings = Settings()
    process = PriceProcess(
        initial_price=100.0, drift=0.0, volatility=0.30, dt=settings.dt, seed=42
    )
    engine = SimulationEngine(price_process=process, settings=settings)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})

    seen: list[tuple[int, int]] = []
    original = engine._build_view

    def spy(step, reference, state):
        view = original(step, reference, state)
        seen.append((step, len(view.price_history)))
        return view

    engine._build_view = spy  # type: ignore[method-assign]
    engine.run(50)

    assert seen, "precondition: views were actually built"
    for step, history_length in seen:
        assert history_length == step - 1, (
            f"at step {step} the agent saw {history_length} observations; "
            "anything at or above the step index means it can read its own outcome"
        )


def test_the_first_step_has_no_history_at_all():
    settings = Settings()
    process = PriceProcess(
        initial_price=100.0, drift=0.0, volatility=0.30, dt=settings.dt, seed=42
    )
    engine = SimulationEngine(price_process=process, settings=settings)
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    view = engine._build_view(1, 100.0, engine.states["mom"])
    assert view.price_history == ()
    assert engine.states["mom"].agent.decide(view) == [], (
        "an agent with no history must stand down, not guess"
    )


def test_all_agents_in_a_step_see_the_identical_book():
    """Otherwise the second agent to act reacts to the first one within the step."""
    settings = Settings()
    process = PriceProcess(
        initial_price=100.0, drift=0.0, volatility=0.30, dt=settings.dt, seed=42
    )
    engine = SimulationEngine(price_process=process, settings=settings)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    engine.add_agent("rev", AgentType.MEAN_REVERSION, {})

    captured: dict[int, list[tuple]] = {}
    original = engine._build_view

    def spy(step, reference, state):
        view = original(step, reference, state)
        captured.setdefault(step, []).append(
            (view.best_bid, view.best_ask, view.mid_price, tuple(view.price_history))
        )
        return view

    engine._build_view = spy  # type: ignore[method-assign]
    engine.run(60)

    for step, views in captured.items():
        assert len(set(views)) == 1, (
            f"agents saw different market state within step {step}: whoever acted "
            "later had a one-step information advantage"
        )
