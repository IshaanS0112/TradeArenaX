"""GBM path and volatility shock tests."""

from __future__ import annotations

import math

import pytest

from app.services.price_process import PriceProcess, VolatilityShock

DT = 1.0 / 98_280


def process(**kwargs) -> PriceProcess:
    defaults = dict(initial_price=100.0, drift=0.0, volatility=0.30, dt=DT, seed=42)
    defaults.update(kwargs)
    return PriceProcess(**defaults)


def test_same_seed_reproduces_the_same_path():
    a = [process(seed=7).advance() for _ in range(50)]
    b = [process(seed=7).advance() for _ in range(50)]
    # Rebuild both fully rather than reusing one generator.
    p1, p2 = process(seed=7), process(seed=7)
    a = [p1.advance() for _ in range(50)]
    b = [p2.advance() for _ in range(50)]
    assert a == b, "an unseeded path makes every agent comparison a different experiment"


def test_different_seeds_diverge():
    p1, p2 = process(seed=1), process(seed=2)
    a = [p1.advance() for _ in range(50)]
    b = [p2.advance() for _ in range(50)]
    assert a != b


def test_price_stays_positive_under_high_volatility():
    """The reason the exact log solution is used instead of an Euler step.

    Euler (``S*(1 + mu*dt + sigma*sqrt(dt)*Z)``) goes negative as soon as
    ``sigma*sqrt(dt)*Z < -1``, which at sigma=3 and a daily step needs only a
    ~5-sigma draw. An exponential cannot go negative at any draw.
    """
    p = process(volatility=3.0, dt=1 / 252, seed=3)
    prices = [p.advance() for _ in range(5000)]
    assert all(price > 0 for price in prices)
    assert min(prices) < 100.0, "precondition: the path really did fall a long way"


def test_degenerate_parameters_fail_loudly_instead_of_producing_nan():
    """Positive in theory is not the same as representable in float64.

    At sigma=8 with dt=0.05 the -sigma^2/2 log-drift is -1.6 per step, so exp()
    underflows to exactly 0.0 within a few thousand steps and every downstream
    log and division turns into nan. That has to raise, not propagate.
    """
    p = process(volatility=8.0, dt=0.05, seed=3)
    with pytest.raises(ValueError, match="degenerated at step"):
        for _ in range(5000):
            p.advance()


def test_zero_volatility_gives_a_deterministic_drift_path():
    p = process(volatility=0.0, drift=0.5, dt=0.01)
    first = p.advance()
    expected = 100.0 * math.exp(0.5 * 0.01)
    assert first == pytest.approx(expected)


def test_history_tracks_every_step_plus_the_initial_price():
    p = process()
    for _ in range(10):
        p.advance()
    assert len(p.history) == 11
    assert p.history[0] == 100.0
    assert p.history[-1] == p.price
    assert p.step_index == 10


def test_realized_volatility_recovers_the_configured_sigma():
    p = process(volatility=0.30, seed=11)
    for _ in range(20_000):
        p.advance()
    realized = p.realized_volatility()
    assert realized is not None
    assert realized == pytest.approx(0.30, rel=0.05), (
        "if the estimator cannot recover sigma from a long path, the "
        "discretisation is wrong"
    )


def test_realized_volatility_needs_a_sample():
    p = process()
    assert p.realized_volatility() is None
    p.advance()
    assert p.realized_volatility() is None


def test_shock_applies_a_jump_at_the_configured_step():
    shock = VolatilityShock(step=5, magnitude_pct=-10.0)
    shocked = process(volatility=0.0, shocks=(shock,))
    clean = process(volatility=0.0)

    for _ in range(4):
        shocked.advance()
        clean.advance()

    assert shocked.price == pytest.approx(clean.price)

    shocked.advance()
    clean.advance()
    assert shocked.price == pytest.approx(clean.price * 0.90), (
        "the jump multiplies the diffused price, it does not replace it"
    )


def test_shock_leaves_elevated_volatility_that_decays():
    shock = VolatilityShock(
        step=10, magnitude_pct=0.0, vol_multiplier=4.0, vol_half_life_steps=10
    )
    p = process(volatility=0.20, shocks=(shock,))

    for _ in range(9):
        p.advance()
    assert p.current_volatility() == pytest.approx(0.20)

    p.advance()  # step 10: shock fires
    assert p.current_volatility() == pytest.approx(0.80), "4x on impact"

    for _ in range(10):
        p.advance()
    # One half-life later the *excess* over baseline has halved: 0.20 + 0.60/2.
    assert p.current_volatility() == pytest.approx(0.50, rel=1e-6)

    for _ in range(200):
        p.advance()
    assert p.current_volatility() == pytest.approx(0.20, rel=1e-3), "decays to baseline"


def test_shock_without_a_vol_multiplier_does_not_change_volatility():
    p = process(volatility=0.25, shocks=(VolatilityShock(step=2, magnitude_pct=5.0),))
    p.advance()
    p.advance()
    assert p.current_volatility() == pytest.approx(0.25)


def test_multiple_shocks_compound_their_volatility_effect():
    shocks = (
        VolatilityShock(step=5, magnitude_pct=0.0, vol_multiplier=2.0, vol_half_life_steps=100),
        VolatilityShock(step=6, magnitude_pct=0.0, vol_multiplier=2.0, vol_half_life_steps=100),
    )
    p = process(volatility=0.10, shocks=shocks)
    for _ in range(6):
        p.advance()
    # Both still near full strength: roughly 0.10 * 2 * 2.
    assert p.current_volatility() > 0.35


# ------------------------------------------------------------------ validation
def test_rejects_a_total_wipeout_shock():
    with pytest.raises(ValueError, match="zero the price"):
        VolatilityShock(step=1, magnitude_pct=-100.0)


def test_rejects_a_calming_shock():
    with pytest.raises(ValueError, match="cannot calm a market"):
        VolatilityShock(step=1, magnitude_pct=1.0, vol_multiplier=0.5)


def test_rejects_a_negative_step():
    with pytest.raises(ValueError):
        VolatilityShock(step=-1, magnitude_pct=1.0)


def test_rejects_two_shocks_at_the_same_step():
    with pytest.raises(ValueError, match="two shocks"):
        process(
            shocks=(
                VolatilityShock(step=5, magnitude_pct=1.0),
                VolatilityShock(step=5, magnitude_pct=2.0),
            )
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initial_price": 0.0},
        {"initial_price": -1.0},
        {"volatility": -0.1},
        {"dt": 0.0},
        {"dt": -1.0},
    ],
)
def test_rejects_impossible_parameters(kwargs):
    with pytest.raises(ValueError):
        process(**kwargs)
