"""Adverse selection, measured rather than asserted."""

from __future__ import annotations

import pytest

from app.enums import AgentType, Side
from app.services.event_engine import EventDrivenEngine
from app.services.microstructure import (
    aggregate,
    direction_of,
    measure_trades,
    summarise_by_agent,
)
from app.services.price_process import PriceProcess, VolatilityShock


class _Fill:
    """Just enough of a Fill for the decomposition, built by hand."""

    def __init__(self, step, price_ticks, quantity, aggressor_side, maker="mm", taker="mom"):
        self.step = step
        self.price_ticks = price_ticks
        self.quantity = quantity
        self.aggressor_side = aggressor_side
        self.maker_agent_id = maker
        self.taker_agent_id = taker


def test_direction_is_the_aggressor_side():
    assert direction_of(Side.BUY) == 1
    assert direction_of(Side.SELL) == -1
    assert direction_of("BUY") == 1


def test_the_identity_holds_per_trade_on_a_constructed_case():
    """A buyer-initiated trade above the mid, into a market that keeps rising."""
    mids = [100.00, 100.05, 100.10, 100.20, 100.30]
    fills = [_Fill(step=1, price_ticks=10_002, quantity=10, aggressor_side=Side.BUY)]

    measured = measure_trades(fills, mids, tick_size=0.01, horizons=(1, 3))[0]

    effective, realised, impact = measured.at(1)
    assert effective == pytest.approx(100.02 - 100.00)
    assert realised == pytest.approx(100.02 - 100.05)
    assert impact == pytest.approx(100.05 - 100.00)
    assert effective == pytest.approx(realised + impact, abs=1e-12)

    effective3, realised3, impact3 = measured.at(3)
    assert impact3 > impact, "impact grows with the horizon when the price trends"
    assert effective3 == pytest.approx(realised3 + impact3, abs=1e-12)


def test_seller_initiated_trades_flip_the_sign():
    mids = [100.00, 99.90]
    fills = [_Fill(step=1, price_ticks=9_998, quantity=5, aggressor_side=Side.SELL)]
    effective, realised, impact = measure_trades(fills, mids, 0.01, horizons=(1,))[0].at(1)

    assert effective == pytest.approx(-(99.98 - 100.00))
    assert impact == pytest.approx(-(99.90 - 100.00)), "a fall after a sale is impact"
    assert impact > 0, "impact is positive when the market moves the aggressor's way"
    assert effective == pytest.approx(realised + impact, abs=1e-12)


def test_a_trade_with_no_mid_produces_no_measure_rather_than_a_number():
    """One-sided books have no mid, and inventing one would be worse."""
    mids = [None, 100.0, None]
    fills = [
        _Fill(step=1, price_ticks=10_000, quantity=1, aggressor_side=Side.BUY),
        _Fill(step=2, price_ticks=10_000, quantity=1, aggressor_side=Side.BUY),
    ]
    measured = measure_trades(fills, mids, 0.01, horizons=(1,))

    assert measured[0].at(1) is None, "no mid at the trade means no decomposition"
    assert measured[1].at(1) is None, "no mid at the horizon means no decomposition"


def _run(settings, steps=260):
    process = PriceProcess(
        initial_price=100.0,
        drift=0.0,
        volatility=0.35,
        dt=settings.dt,
        seed=42,
        shocks=(
            VolatilityShock(
                step=120, magnitude_pct=-8.0, vol_multiplier=3.0, vol_half_life_steps=40
            ),
        ),
    )
    engine = EventDrivenEngine(price_process=process, settings=settings)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    engine.add_agent("rev", AgentType.MEAN_REVERSION, {})
    return engine, engine.run(steps)


def test_identity_holds_for_every_trade_of_a_real_run(settings):
    engine, result = _run(settings)
    mids = [rec.mid_price for rec in result.step_records]
    measures = measure_trades(result.fills, mids, settings.tick_size, horizons=(1, 5, 20))

    checked = 0
    for measure in measures:
        for horizon in (1, 5, 20):
            values = measure.at(horizon)
            if values is None:
                continue
            effective, realised, impact = values
            assert effective == pytest.approx(realised + impact, abs=1e-9)
            checked += 1
    assert checked > 50, "the run should produce plenty of measurable trades"


def test_maker_and_taker_rows_of_the_same_trade_sum_to_zero(settings):
    """What the aggressor pays is what the passive side earns."""
    _, result = _run(settings)
    mids = [rec.mid_price for rec in result.step_records]
    measures = measure_trades(result.fills, mids, settings.tick_size, horizons=(5,))
    rows = summarise_by_agent(measures, 5)
    assert rows, "a run with trades must produce rows"

    total_effective = sum(r.effective_half_spread * r.quantity for r in rows)
    total_impact = sum(r.price_impact * r.quantity for r in rows)
    assert total_effective == pytest.approx(0.0, abs=1e-6)
    assert total_impact == pytest.approx(0.0, abs=1e-6)


def test_the_market_maker_earns_the_spread_and_pays_impact(settings):
    """The decomposition should describe a maker's business the way it works."""
    _, result = _run(settings)
    mids = [rec.mid_price for rec in result.step_records]
    measures = measure_trades(result.fills, mids, settings.tick_size, horizons=(5,))
    rows = {(r.agent_id, r.role): r for r in summarise_by_agent(measures, 5)}

    maker = rows[("mm", "maker")]
    taker = rows[("mom", "taker")]
    assert maker.trade_count > 0
    assert maker.effective_half_spread > 0, "the passive side earns the quoted spread"
    assert maker.price_impact > 0, "and gives some of it back to informed flow"
    assert maker.realised_half_spread == pytest.approx(
        maker.effective_half_spread - maker.price_impact, abs=1e-9
    )
    assert taker.effective_half_spread < 0, "the aggressor pays for immediacy"


def test_market_aggregate_reports_bps_and_counts(settings):
    _, result = _run(settings)
    mids = [rec.mid_price for rec in result.step_records]
    measures = measure_trades(result.fills, mids, settings.tick_size, horizons=(5,))
    summary = aggregate(measures, 5)

    assert summary["trade_count"] > 0
    assert summary["effective_bps"] == pytest.approx(
        10_000 * summary["effective_half_spread"] / 100.0, rel=0.3
    )
    assert summary["effective_half_spread"] == pytest.approx(
        summary["realised_half_spread"] + summary["price_impact"], abs=1e-9
    )


def test_aggregate_of_nothing_is_none_not_zero():
    """A metric with no data returns None, never a confident zero."""
    summary = aggregate([], 5)
    assert summary["trade_count"] == 0
    assert summary["effective_half_spread"] is None
    assert summary["impact_bps"] is None
