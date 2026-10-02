"""The options maker: banded hedging, and where the money actually went."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.config import get_settings
from app.enums import AgentType, OrderType, Side
from app.services.agents import build_agent
from app.services.agents.base import MarketView
from app.services.agents.options_maker import OptionContract, OptionsMarketMakerAgent
from app.services.derivatives.pricing import BlackScholesInputs, greeks
from app.services.event_engine import EventDrivenEngine
from app.services.price_process import PriceProcess, VolatilityShock

SETTINGS = get_settings()


def _maker(**config) -> OptionsMarketMakerAgent:
    return build_agent("opt", AgentType.OPTIONS_MAKER, config)  # type: ignore[return-value]


def _view(step=1, spot=100.0, inventory=0.0, spread=0.10):
    return MarketView(
        step=step,
        best_bid=spot - spread / 2,
        best_ask=spot + spread / 2,
        mid_price=spot,
        reference_price=spot,
        price_history=tuple([spot] * max(step - 1, 0)),
        trade_prices=(),
        inventory=inventory,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        tick_size=0.01,
        bid_quantity=50.0,
        ask_quantity=50.0,
    )


# ---------------------------------------------------------------- the quotes
def test_the_strip_is_built_once_around_the_opening_spot():
    maker = _maker(strike_offsets=(0.9, 1.0, 1.1))
    maker.decide(_view(step=1, spot=100.0))
    strikes = sorted({c.strike for c in maker._contracts})

    assert strikes == [90.0, 100.0, 110.0]
    maker.decide(_view(step=2, spot=130.0))
    assert sorted({c.strike for c in maker._contracts}) == strikes, "the strip is fixed"


def test_quotes_bracket_the_model_price_and_widen_with_vega_and_gamma():
    maker = _maker(vega_spread_k=0.5, gamma_spread_k=1.0)
    maker.decide(_view(step=1, spot=100.0))
    contract = next(c for c in maker._contracts if c.option_type == "CALL" and c.strike == 100.0)

    bid, ask = maker.quote(contract, 100.0, step=1)
    model = greeks(
        BlackScholesInputs(
            spot=100.0,
            strike=100.0,
            tau=maker._tau(contract, 1),
            volatility=0.30,
            option_type="CALL",
        )
    ).price

    assert bid < model < ask
    tight = _maker(vega_spread_k=0.0, gamma_spread_k=0.0)
    tight.decide(_view(step=1, spot=100.0))
    tight_bid, tight_ask = tight.quote(contract, 100.0, step=1)
    assert (ask - bid) > (tight_ask - tight_bid), "vega and gamma have to widen it"


# --------------------------------------------------------------- the hedging
def test_no_hedge_while_delta_sits_inside_the_band():
    maker = _maker(client_intensity=0.0, hedge_band=25.0)
    maker.decide(_view(step=1))
    maker.positions[maker._contracts[0].key] = 1.0  # a small delta

    intents = maker.decide(_view(step=2))
    snapshot = maker.snapshots[-1]
    assert abs(snapshot.portfolio_delta) < snapshot.hedge_band
    assert intents == [], "inside the band, doing nothing is the optimal policy"


def test_a_breach_hedges_back_to_the_edge_of_the_band_not_to_zero():
    maker = _maker(client_intensity=0.0, hedge_band=10.0, contract_multiplier=100.0)
    maker.decide(_view(step=1))
    call = next(c for c in maker._contracts if c.option_type == "CALL")
    maker.positions[call.key] = 5.0  # long calls: long delta

    intents = maker.decide(_view(step=2))
    assert len(intents) == 1
    hedge = intents[0]
    assert hedge.side is Side.SELL, "long delta is hedged by selling the underlying"
    assert hedge.order_type is OrderType.MARKET, "a hedge that rests is not a hedge"

    delta = maker.snapshots[-1].portfolio_delta
    assert hedge.quantity == pytest.approx(abs(delta) - 10.0, rel=1e-6)


def test_a_wider_band_trades_less_and_carries_more_delta_risk():
    """The trade-off the band exists to make, measured rather than asserted."""
    outcomes = []
    for band in (5.0, 25.0, 75.0, 200.0):
        maker = _maker(hedge_band=band, client_intensity=0.35, random_seed=3)
        inventory = 0.0
        deltas: list[float] = []
        trades = 0
        for step in range(1, 200):
            spot = 100.0 + 8.0 * math.sin(step / 25.0)
            intents = maker.decide(_view(step=step, spot=spot, inventory=inventory))
            for intent in intents:
                inventory += intent.quantity if intent.side is Side.BUY else -intent.quantity
                trades += 1
            deltas.append(maker.snapshots[-1].portfolio_delta + inventory)
        outcomes.append((band, trades, float(np.std(deltas))))

    trades = [o[1] for o in outcomes]
    variance = [o[2] for o in outcomes]

    assert trades == sorted(trades, reverse=True), f"wider band must trade less: {outcomes}"
    assert variance[-1] > variance[0], f"wider band must carry more delta: {outcomes}"

    # Monotone only while the band is still binding.
    binding = [(band, var) for (band, count, var) in outcomes if count > 0]
    assert [var for _, var in binding] == sorted(var for _, var in binding), binding


def test_the_auto_band_follows_the_whalley_wilmott_cube_root():
    maker = _maker(hedge_band_mode="auto", transaction_cost=0.02, risk_aversion=0.5)
    band = maker.hedge_band(spot=100.0, gamma=4.0)
    expected = (1.5 * 0.02 * 100.0 * 16.0 / 0.5) ** (1 / 3)
    assert band == pytest.approx(expected)
    assert maker.hedge_band(100.0, 8.0) > band, "more gamma, wider band"


# ------------------------------------------------------------- the attribution
def test_gamma_pnl_tracks_half_gamma_s_squared_over_a_known_path():
    """The formula the whole derivatives layer exists to make visible."""
    maker = _maker(client_intensity=0.0, hedge_band=1e9, contract_multiplier=100.0)
    maker.decide(_view(step=1, spot=100.0))
    call = next(c for c in maker._contracts if c.option_type == "CALL" and c.strike == 100.0)
    maker.positions[call.key] = 10.0

    maker.decide(_view(step=2, spot=100.0))
    gamma_before = maker.snapshots[-1].portfolio_gamma

    move = 0.75
    maker.decide(_view(step=3, spot=100.0 + move))
    snapshot = maker.snapshots[-1]

    assert snapshot.gamma_pnl == pytest.approx(0.5 * gamma_before * move**2, rel=1e-9)
    assert snapshot.gamma_pnl > 0, "a long gamma position profits from movement"


def test_a_short_gamma_book_loses_when_the_underlying_moves():
    maker = _maker(client_intensity=0.0, hedge_band=1e9, contract_multiplier=100.0)
    maker.decide(_view(step=1, spot=100.0))
    call = next(c for c in maker._contracts if c.option_type == "CALL" and c.strike == 100.0)
    maker.positions[call.key] = -10.0  # sold the calls

    maker.decide(_view(step=2, spot=100.0))
    maker.decide(_view(step=3, spot=101.0))
    assert maker.snapshots[-1].gamma_pnl < 0


def test_theta_is_negative_for_a_long_book_and_positive_for_a_short_one():
    for position, expected_sign in ((5.0, -1), (-5.0, 1)):
        maker = _maker(client_intensity=0.0, hedge_band=1e9)
        maker.decide(_view(step=1))
        maker.positions[maker._contracts[0].key] = position
        maker.decide(_view(step=2))
        maker.decide(_view(step=3))
        assert math.copysign(1, maker.snapshots[-1].theta_pnl) == expected_sign


def test_hedging_slippage_is_recorded_as_a_cost():
    maker = _maker(client_intensity=0.0, hedge_band=1.0, contract_multiplier=100.0)
    maker.decide(_view(step=1))
    call = next(c for c in maker._contracts if c.option_type == "CALL")
    maker.positions[call.key] = 5.0

    maker.decide(_view(step=2, spread=0.20))
    assert maker.snapshots[-1].hedge_slippage < 0, "crossing the spread is not free"


def test_attribution_totals_match_the_series():
    maker = _maker(client_intensity=0.4, random_seed=9)
    for step in range(1, 80):
        maker.decide(_view(step=step, spot=100.0 + 0.05 * step))

    attribution = maker.attribution()
    assert attribution["gamma_pnl"] == pytest.approx(
        sum(s["gamma_pnl"] for s in attribution["snapshots"])
    )
    assert attribution["option_premium"] == pytest.approx(maker.option_premium)
    assert len(attribution["snapshots"]) == 79


# -------------------------------------------------------- inside the engine
def test_the_hedger_trades_the_equity_book_and_conservation_still_holds():
    """The point of the whole layer: the two books interact."""
    process = PriceProcess(
        initial_price=100.0,
        drift=0.0,
        volatility=0.35,
        dt=SETTINGS.dt,
        seed=42,
        shocks=(
            VolatilityShock(
                step=120, magnitude_pct=-7.0, vol_multiplier=3.0, vol_half_life_steps=40
            ),
        ),
    )
    engine = EventDrivenEngine(price_process=process, settings=SETTINGS)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("opt", AgentType.OPTIONS_MAKER, {"client_intensity": 0.2, "hedge_band": 20.0})
    engine.add_agent("noise", AgentType.NOISE_TRADER, {"activity": 0.5})
    result = engine.run(250)

    options_agent = result.agent_states["opt"].agent
    assert isinstance(options_agent, OptionsMarketMakerAgent)
    attribution = options_agent.attribution()
    assert attribution["hedge_trades"] > 0, "the hedger must reach the equity book"
    assert result.agent_states["opt"].tracker.fill_count > 0

    mark = engine._mark_price()
    total = sum(
        state.tracker.realized_pnl + state.tracker.unrealized_pnl(mark)
        for state in result.agent_states.values()
    )
    fees = sum(state.tracker.fees_paid for state in result.agent_states.values())
    assert abs(total + fees) < 1e-6, "the equity invariant survives an options hedger"


def test_option_premium_is_kept_out_of_the_equity_accounting():
    """Folding it in would break the invariant every other result rests."""
    process = PriceProcess(
        initial_price=100.0, drift=0.0, volatility=0.3, dt=SETTINGS.dt, seed=7
    )
    engine = EventDrivenEngine(price_process=process, settings=SETTINGS)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("opt", AgentType.OPTIONS_MAKER, {"client_intensity": 0.5})
    result = engine.run(120)

    agent = result.agent_states["opt"].agent
    assert agent.option_premium != 0.0, "clients traded, so premium changed hands"
    equity_pnl = result.summaries["opt"].total_pnl
    assert equity_pnl != agent.option_premium


def test_config_is_validated_strictly():
    with pytest.raises(ValueError, match="implied_volatility"):
        _maker(implied_volatility=0.0)
    with pytest.raises(ValueError, match="hedge_band_mode"):
        _maker(hedge_band_mode="guess")
    with pytest.raises(ValueError, match="client_intensity"):
        _maker(client_intensity=1.5)
    with pytest.raises(ValueError, match="unknown config keys"):
        _maker(hedge_bands=3)


def test_contract_keys_are_stable_and_unique():
    call = OptionContract(strike=100.0, expiry_step=500, option_type="CALL")
    put = OptionContract(strike=100.0, expiry_step=500, option_type="PUT")
    assert call.key != put.key
    assert call.key == OptionContract(100.0, 500, "CALL").key
