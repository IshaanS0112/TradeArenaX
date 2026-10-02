"""Microprice: the properties it must have, and what it is worth here."""

from __future__ import annotations

import numpy as np
import pytest

from app.config import get_settings
from app.enums import AgentType
from app.services.event_engine import EventDrivenEngine
from app.services.microprice import (
    MicropriceEstimator,
    imbalance,
    weighted_microprice,
)
from app.services.price_process import PriceProcess

SETTINGS = get_settings()


# ------------------------------------------------------------- the weighted form
def test_microprice_equals_the_mid_at_perfect_balance():
    assert weighted_microprice(99.99, 100.01, 100, 100) == pytest.approx(100.00)


def test_microprice_is_monotonic_in_imbalance():
    """Heavier bid pulls fair value towards the ask - the direction people flip."""
    values = [
        weighted_microprice(99.99, 100.01, bid, 100 - bid) for bid in (0, 25, 50, 75, 100)
    ]
    assert values == sorted(values)
    assert values[0] == pytest.approx(99.99), "all ask: fair value sits on the bid"
    assert values[-1] == pytest.approx(100.01), "all bid: fair value sits on the ask"


def test_microprice_is_bracketed_by_the_touch():
    rng = np.random.default_rng(3)
    for _ in range(500):
        bid_qty, ask_qty = rng.uniform(0, 500, 2)
        value = weighted_microprice(99.90, 100.10, bid_qty, ask_qty)
        assert 99.90 <= value <= 100.10


def test_microprice_needs_two_sides():
    assert weighted_microprice(None, 100.01, 10, 10) is None
    assert weighted_microprice(99.99, None, 10, 10) is None


def test_an_empty_touch_falls_back_to_the_mid():
    assert weighted_microprice(99.99, 100.01, 0, 0) == pytest.approx(100.0)


def test_imbalance_is_a_half_when_the_book_is_empty():
    assert imbalance(0, 0) == 0.5
    assert imbalance(75, 25) == 0.75


# ------------------------------------------------------------------ the estimator
def _synthetic_series(n: int = 6_000, seed: int = 5) -> list[tuple[float, int, float]]:
    """A market where imbalance genuinely predicts the next move."""
    rng = np.random.default_rng(seed)
    mid = 100.0
    out: list[tuple[float, int, float]] = []
    for _ in range(n):
        imb = float(rng.uniform(0, 1))
        out.append((imb, 2, mid))
        mid = round(mid + (imb - 0.5) * 0.02 + rng.normal(0, 0.002), 6)
    return out


def test_the_estimator_learns_a_relationship_that_is_really_there():
    estimator = MicropriceEstimator(min_observations=30)
    estimator.observe_series(_synthetic_series()).fit()
    assert estimator.fitted

    tick = 0.01
    heavy_bid = estimator.fair_value(99.99, 100.01, 900, 100, tick)
    heavy_ask = estimator.fair_value(99.99, 100.01, 100, 900, tick)

    assert heavy_bid > heavy_ask, "the fitted adjustment must follow the data"
    assert 99.99 <= heavy_ask <= heavy_bid <= 100.01, "and stay inside the touch"


def test_an_unfitted_estimator_falls_back_to_the_weighted_form():
    """A maker still has to quote on step one."""
    estimator = MicropriceEstimator()
    assert not estimator.fitted
    value = estimator.fair_value(99.99, 100.01, 300, 100, 0.01)
    assert value == pytest.approx(weighted_microprice(99.99, 100.01, 300, 100))


def test_states_with_too_little_data_are_not_fitted():
    estimator = MicropriceEstimator(min_observations=1_000)
    estimator.observe_series(_synthetic_series(n=500)).fit()
    assert not estimator.fitted, "twenty observations is not an estimate"


def test_repeated_identical_states_are_not_counted_as_transitions():
    """Sampling a still book teaches the model that the price never moves."""
    still = [(0.9, 2, 100.0)] * 500
    estimator = MicropriceEstimator(min_observations=5).observe_series(still).fit()
    assert estimator.summary()["transitions"] == 0
    assert not estimator.fitted


# ------------------------------------------------------- the finding, pinned down
def _book_samples(seed: int, steps: int = 800) -> list[tuple[float, float, float, float, float]]:
    process = PriceProcess(
        initial_price=100.0, drift=0.0, volatility=0.30, dt=SETTINGS.dt, seed=seed
    )
    engine = EventDrivenEngine(price_process=process, settings=SETTINGS)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {"fair_value_source": "mid"})
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    engine.add_agent("rev", AgentType.MEAN_REVERSION, {})
    engine.add_agent("noise", AgentType.NOISE_TRADER, {"activity": 0.6})
    engine.run(steps)
    return [
        (pub.best_bid, pub.best_ask, pub.bid_quantity, pub.ask_quantity, pub.mid_price)
        for pub in engine.publications
        if pub.mid_price is not None
    ]


def test_in_this_market_imbalance_does_not_predict_the_next_move():
    """The measured finding, and the reason the maker does not default."""
    samples = _book_samples(42)
    mids = np.array([s[4] for s in samples])
    imbalances = np.array([imbalance(s[2], s[3]) for s in samples])
    moves = mids[1:] - mids[:-1]

    correlation = float(np.corrcoef(imbalances[:-1], moves)[0, 1])
    assert correlation < 0.05, (
        f"imbalance now predicts the next mid move (corr={correlation:.3f}); "
        "the microprice finding in the README needs revisiting"
    )


def test_microprice_is_not_a_better_fair_value_here_than_the_mid():
    samples = _book_samples(43)
    mids = np.array([s[4] for s in samples])
    micro = np.array([weighted_microprice(*s[:4]) for s in samples])

    mid_rmse = float(np.sqrt(((mids[1:] - mids[:-1]) ** 2).mean()))
    micro_rmse = float(np.sqrt(((mids[1:] - micro[:-1]) ** 2).mean()))

    assert micro_rmse >= mid_rmse * 0.98, (
        f"microprice RMSE {micro_rmse:.5f} now beats the mid's {mid_rmse:.5f} - "
        "which would be a genuine improvement and a change to the write-up"
    )


def test_the_maker_can_be_switched_between_fair_values_on_identical_seeds():
    """The comparison harness itself: same path, two definitions of fair value."""
    outcomes = {}
    for source in ("reference", "mid", "microprice"):
        process = PriceProcess(
            initial_price=100.0, drift=0.0, volatility=0.30, dt=SETTINGS.dt, seed=7
        )
        engine = EventDrivenEngine(price_process=process, settings=SETTINGS)
        engine.add_agent("mm", AgentType.MARKET_MAKER, {"fair_value_source": source})
        engine.add_agent("mom", AgentType.MOMENTUM, {})
        engine.add_agent("noise", AgentType.NOISE_TRADER, {"activity": 0.5})
        result = engine.run(200)
        outcomes[source] = result.summaries["mm"].total_pnl

    assert len(set(round(v, 6) for v in outcomes.values())) > 1, (
        "three different fair values on one path should not produce one answer"
    )


def test_an_unknown_fair_value_source_is_rejected():
    from app.services.agents import build_agent

    with pytest.raises(ValueError, match="fair_value_source"):
        build_agent("mm", AgentType.MARKET_MAKER, {"fair_value_source": "vibes"})
