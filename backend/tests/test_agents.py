"""Agent logic tests.

Every one of these builds a MarketView by hand and asserts on the returned
intents. No simulation runs, no book, no database - which is the point of making
``decide()`` a pure function of the view.
"""

from __future__ import annotations

import pytest

from app.enums import AgentType, OrderType, Side
from app.services.agents import (
    MarketMakerAgent,
    MeanReversionAgent,
    MomentumAgent,
    build_agent,
    default_config,
)
from app.services.agents.base import MarketView


def view(**overrides) -> MarketView:
    defaults = dict(
        step=100,
        best_bid=99.95,
        best_ask=100.05,
        mid_price=100.0,
        reference_price=100.0,
        price_history=tuple([100.0] * 50),
        trade_prices=(),
        inventory=0.0,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        tick_size=0.01,
    )
    defaults.update(overrides)
    return MarketView(**defaults)


def ramp(start: float, end: float, n: int) -> tuple[float, ...]:
    step = (end - start) / (n - 1)
    return tuple(start + i * step for i in range(n))


# ============================================================== market maker
def mm(**config) -> MarketMakerAgent:
    return build_agent("mm", AgentType.MARKET_MAKER, config)


def test_maker_quotes_both_sides_symmetrically_when_flat():
    agent = mm(spread=0.10, volatility_widening=False)
    intents = agent.decide(view(reference_price=100.0, inventory=0.0))

    assert {i.side for i in intents} == {Side.BUY, Side.SELL}
    bid = next(i for i in intents if i.side is Side.BUY)
    ask = next(i for i in intents if i.side is Side.SELL)
    assert bid.price == pytest.approx(99.95)
    assert ask.price == pytest.approx(100.05)
    assert all(i.order_type is OrderType.LIMIT for i in intents), "a maker posts, it does not take"


def test_long_inventory_skews_both_quotes_down():
    """The behaviour that distinguishes inventory management from spread widening.

    Long inventory shifts the centre of the quote down - the bid gets less
    attractive to sellers and the ask gets more attractive to buyers - so the
    flow the maker attracts reduces the position. The *width* is unchanged.
    """
    agent = mm(spread=0.10, inventory_skew_k=0.01, volatility_widening=False)
    flat = agent.decide(view(inventory=0.0))
    long_ = agent.decide(view(inventory=50.0))

    flat_bid = next(i.price for i in flat if i.side is Side.BUY)
    flat_ask = next(i.price for i in flat if i.side is Side.SELL)
    long_bid = next(i.price for i in long_ if i.side is Side.BUY)
    long_ask = next(i.price for i in long_ if i.side is Side.SELL)

    assert long_bid < flat_bid and long_ask < flat_ask
    assert (long_ask - long_bid) == pytest.approx(flat_ask - flat_bid), (
        "inventory skew shifts the quote, it does not widen it"
    )
    assert flat_bid - long_bid == pytest.approx(0.5), "k * inventory = 0.01 * 50"


def test_short_inventory_skews_both_quotes_up():
    agent = mm(spread=0.10, inventory_skew_k=0.01, volatility_widening=False)
    short = agent.decide(view(inventory=-50.0))
    assert next(i.price for i in short if i.side is Side.BUY) == pytest.approx(100.45)
    assert next(i.price for i in short if i.side is Side.SELL) == pytest.approx(100.55)


def test_maker_goes_one_sided_at_its_risk_cutoff():
    agent = mm(max_inventory=100.0, one_sided_at_risk_score=0.8, volatility_widening=False)
    intents = agent.decide(view(inventory=90.0))

    assert {i.side for i in intents} == {Side.SELL}, (
        "at the limit, price incentives are not enough - it must stop bidding"
    )


def test_maker_stops_offering_when_deeply_short():
    agent = mm(max_inventory=100.0, one_sided_at_risk_score=0.8, volatility_widening=False)
    intents = agent.decide(view(inventory=-90.0))
    assert {i.side for i in intents} == {Side.BUY}


def test_maker_never_quotes_past_its_inventory_limit():
    agent = mm(max_inventory=100.0, quote_size=30.0, volatility_widening=False,
               one_sided_at_risk_score=1.0)
    intents = agent.decide(view(inventory=85.0))
    bid = next((i for i in intents if i.side is Side.BUY), None)
    assert bid is not None and bid.quantity == pytest.approx(15.0)


def test_maker_widens_with_realized_volatility():
    agent = mm(spread=0.10, volatility_widening=True, volatility_widening_k=2.0)
    calm = agent.half_spread(view(price_history=tuple([100.0] * 30)))
    choppy = agent.half_spread(
        view(price_history=tuple(100.0 + (1 if i % 2 else -1) for i in range(30)))
    )
    assert choppy > calm, "a fixed width during a shock is how a maker gets run over"


def test_widening_is_capped():
    agent = mm(spread=0.10, volatility_widening=True, volatility_widening_k=50.0,
               max_spread_multiple=3.0)
    wild = agent.half_spread(
        view(price_history=tuple(100.0 + (20 if i % 2 else -20) for i in range(30)))
    )
    assert wild == pytest.approx(0.05 * 3.0)


def test_maker_falls_back_to_the_base_spread_without_history():
    agent = mm(spread=0.10, volatility_widening=True)
    assert agent.half_spread(view(price_history=(100.0,))) == pytest.approx(0.05)


@pytest.mark.parametrize(
    "config",
    [
        {"spread": 0.0},
        {"spread": -0.1},
        {"quote_size": 0.0},
        {"inventory_skew_k": -0.01},
        {"max_inventory": 0.0},
        {"one_sided_at_risk_score": 0.0},
        {"one_sided_at_risk_score": 1.5},
        {"max_spread_multiple": 0.5},
    ],
)
def test_maker_rejects_incoherent_config(config):
    with pytest.raises(ValueError):
        mm(**config)


# ================================================================== momentum
def mom(**config) -> MomentumAgent:
    return build_agent("mom", AgentType.MOMENTUM, config)


def test_momentum_buys_an_uptrend_and_sells_a_downtrend():
    up = mom(lookback=10, threshold=0.01, cooldown_steps=0)
    intents = up.decide(view(price_history=ramp(100.0, 105.0, 20)))
    assert [i.side for i in intents] == [Side.BUY]

    down = mom(lookback=10, threshold=0.01, cooldown_steps=0)
    intents = down.decide(view(price_history=ramp(105.0, 100.0, 20)))
    assert [i.side for i in intents] == [Side.SELL]


def test_momentum_stays_out_below_its_threshold():
    agent = mom(lookback=10, threshold=0.10, cooldown_steps=0)
    assert agent.decide(view(price_history=ramp(100.0, 101.0, 20))) == []


def test_momentum_needs_enough_history():
    agent = mom(lookback=20, threshold=0.001, cooldown_steps=0)
    assert agent.signal(view(price_history=tuple(range(1, 15)))) is None
    assert agent.decide(view(price_history=ramp(100.0, 110.0, 10))) == []


def test_momentum_scales_size_with_conviction():
    agent = mom(lookback=10, threshold=0.01, base_size=5.0, max_size=25.0,
                size_gain=1.0, cooldown_steps=0)
    # A 20-point ramp to 102.0 gives a 10-step return of ~1.04%, i.e. just over
    # the 1% threshold, so this is deliberately a barely-qualifying signal.
    weak = agent.decide(view(price_history=ramp(100.0, 102.0, 20)))[0]

    fresh = mom(lookback=10, threshold=0.01, base_size=5.0, max_size=25.0,
                size_gain=1.0, cooldown_steps=0)
    strong = fresh.decide(view(price_history=ramp(100.0, 110.0, 20)))[0]

    assert strong.quantity > weak.quantity
    assert weak.quantity == pytest.approx(5.0, rel=0.2), (
        "a signal at the threshold trades base size - conviction has to be earned"
    )


def test_momentum_size_is_capped():
    agent = mom(lookback=10, threshold=0.001, base_size=5.0, max_size=12.0,
                size_gain=10.0, cooldown_steps=0)
    assert agent.decide(view(price_history=ramp(100.0, 200.0, 20)))[0].quantity == 12.0


def test_momentum_respects_its_cooldown():
    agent = mom(lookback=10, threshold=0.01, cooldown_steps=5)
    trend = ramp(100.0, 110.0, 20)

    assert agent.decide(view(step=100, price_history=trend)) != []
    assert agent.decide(view(step=102, price_history=trend)) == [], "still cooling down"
    assert agent.decide(view(step=105, price_history=trend)) != []


def test_momentum_takes_liquidity_by_default():
    agent = mom(lookback=10, threshold=0.01, cooldown_steps=0)
    intent = agent.decide(view(price_history=ramp(100.0, 110.0, 20)))[0]
    assert intent.order_type is OrderType.MARKET
    assert intent.price is None


def test_momentum_in_limit_mode_pays_the_far_touch():
    agent = mom(lookback=10, threshold=0.01, cooldown_steps=0,
                aggressive_order_type="LIMIT")
    intent = agent.decide(
        view(price_history=ramp(100.0, 110.0, 20), best_ask=100.05)
    )[0]
    assert intent.order_type is OrderType.LIMIT
    assert intent.price == pytest.approx(100.05)


def test_momentum_in_limit_mode_stands_down_without_a_touch():
    agent = mom(lookback=10, threshold=0.01, cooldown_steps=0,
                aggressive_order_type="LIMIT")
    assert agent.decide(view(price_history=ramp(100.0, 110.0, 20), best_ask=None)) == []


def test_momentum_will_not_add_past_its_inventory_limit():
    agent = mom(lookback=10, threshold=0.01, cooldown_steps=0, max_inventory=50.0)
    assert agent.decide(view(price_history=ramp(100.0, 110.0, 20), inventory=50.0)) == []


@pytest.mark.parametrize(
    "config",
    [
        {"lookback": 0},
        {"threshold": 0.0},
        {"threshold": -0.1},
        {"base_size": 0.0},
        {"max_size": 1.0, "base_size": 5.0},
        {"size_gain": -1.0},
        {"cooldown_steps": -1},
        {"aggressive_order_type": "ICEBERG"},
    ],
)
def test_momentum_rejects_incoherent_config(config):
    with pytest.raises(ValueError):
        mom(**config)


# ============================================================ mean reversion
def rev(**config) -> MeanReversionAgent:
    return build_agent("rev", AgentType.MEAN_REVERSION, config)


def test_mean_reversion_trades_against_the_deviation():
    """The sign that separates it from momentum, on the identical price series."""
    series = ramp(100.0, 110.0, 40)

    reverter = rev(window=30, z_threshold=0.5, cooldown_steps=0)
    momentum = mom(lookback=30, threshold=0.01, cooldown_steps=0)

    rev_side = reverter.decide(view(price_history=series))[0].side
    mom_side = momentum.decide(view(price_history=series))[0].side

    assert rev_side is Side.SELL
    assert mom_side is Side.BUY
    assert rev_side is not mom_side


def test_mean_reversion_buys_a_dip():
    agent = rev(window=30, z_threshold=0.5, cooldown_steps=0)
    assert agent.decide(view(price_history=ramp(110.0, 100.0, 40)))[0].side is Side.BUY


def test_z_score_guards_against_a_flat_series():
    """A zero moving std would make the z-score infinite; a flat book is common."""
    agent = rev(window=30, z_threshold=1.0, min_dispersion_ticks=1.0)
    assert agent.z_score(view(price_history=tuple([100.0] * 40))) is None
    assert agent.decide(view(price_history=tuple([100.0] * 40))) == []


def test_z_score_needs_at_least_three_observations():
    agent = rev(window=30, z_threshold=1.0)
    assert agent.z_score(view(price_history=(100.0, 101.0))) is None


def test_mean_reversion_stays_out_inside_its_threshold():
    agent = rev(window=30, z_threshold=5.0, cooldown_steps=0)
    assert agent.decide(view(price_history=ramp(100.0, 101.0, 40))) == []


def test_mean_reversion_exits_when_the_deviation_is_worked_off():
    """Without an exit rule the strategy becomes 'buy the dip, forever'."""
    agent = rev(window=10, z_threshold=1.5, exit_z=0.5, cooldown_steps=0)
    # A series whose last point sits essentially on its own mean.
    flat_ish = (99.0, 101.0, 99.0, 101.0, 99.0, 101.0, 99.0, 101.0, 99.0, 100.0)

    intents = agent.decide(view(price_history=flat_ish, inventory=20.0))
    assert len(intents) == 1
    assert intents[0].side is Side.SELL
    assert intents[0].quantity == pytest.approx(20.0), "exit flattens the whole position"
    assert "exit" in intents[0].reason


def test_exit_does_nothing_when_already_flat():
    agent = rev(window=10, z_threshold=1.5, exit_z=0.5, cooldown_steps=0)
    flat_ish = (99.0, 101.0, 99.0, 101.0, 99.0, 101.0, 99.0, 101.0, 99.0, 100.0)
    assert agent.decide(view(price_history=flat_ish, inventory=0.0)) == []


def test_mean_reversion_respects_its_cooldown():
    agent = rev(window=30, z_threshold=0.5, cooldown_steps=5)
    series = ramp(100.0, 110.0, 40)
    assert agent.decide(view(step=100, price_history=series)) != []
    assert agent.decide(view(step=102, price_history=series)) == []


@pytest.mark.parametrize(
    "config",
    [
        {"window": 2},
        {"z_threshold": 0.0},
        {"base_size": 0.0},
        {"min_dispersion_ticks": 0.0},
        {"exit_z": 2.0, "z_threshold": 1.5},
        {"exit_z": -0.1},
        {"aggressive_order_type": "PEG"},
    ],
)
def test_mean_reversion_rejects_incoherent_config(config):
    with pytest.raises(ValueError):
        rev(**config)


# ================================================================== registry
def test_unknown_config_keys_are_rejected_not_ignored():
    """A typo that silently leaves a default in place is an unreproducible run."""
    with pytest.raises(ValueError, match="unknown config keys"):
        mom(threshhold=0.01)


def test_default_config_exposes_every_tunable():
    for agent_type in AgentType:
        config = default_config(agent_type)
        assert "max_inventory" in config
        assert build_agent("x", agent_type, config).config == config, (
            "defaults must round-trip through validation"
        )


def test_build_agent_rejects_an_unknown_type():
    with pytest.raises(ValueError):
        build_agent("x", "SCALPER", {})
