"""Metric tests, including the two cases the textbook one-liners get wrong."""

from __future__ import annotations

import math

import pytest

from app.services.performance_metrics import (
    annualised_volatility,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
    step_returns,
    summarise,
)

CAPITAL = 100_000.0


def test_step_returns_are_relative_to_the_equity_curve():
    rets = step_returns([0.0, 100.0, 200.0], CAPITAL)
    assert rets[0] == pytest.approx(100.0 / CAPITAL)
    assert rets[1] == pytest.approx(100.0 / (CAPITAL + 100.0))


def test_sharpe_matches_a_hand_computation():
    pnl = [0.0, 100.0, 50.0, 150.0, 200.0]
    rets = step_returns(pnl, CAPITAL)
    expected_per_step = float(rets.mean()) / float(rets.std(ddof=1))

    annual, per_step = sharpe_ratio(pnl, CAPITAL, 0.0, steps_per_year=98_280)
    assert per_step == pytest.approx(expected_per_step)
    assert annual == pytest.approx(expected_per_step * math.sqrt(98_280))


def test_sharpe_is_none_for_a_flat_pnl_series():
    """Zero dispersion is an agent that did not trade, not an infinite Sharpe."""
    annual, per_step = sharpe_ratio([0.0] * 10, CAPITAL, 0.0, 98_280)
    assert annual is None and per_step is None


def test_sharpe_is_none_with_too_little_data():
    assert sharpe_ratio([5.0], CAPITAL, 0.0, 98_280) == (None, None)


def test_risk_free_rate_reduces_sharpe():
    pnl = [0.0, 100.0, 200.0, 300.0]
    high, _ = sharpe_ratio(pnl, CAPITAL, 0.0, 98_280)
    low, _ = sharpe_ratio(pnl, CAPITAL, 0.0005, 98_280)
    assert low < high


def test_max_drawdown_on_a_known_path():
    # Equity: 100000, 100500, 100200, 100800. Worst decline is 500 - 200 = 300
    # from a peak of 100500.
    pct, absolute = max_drawdown([0.0, 500.0, 200.0, 800.0], CAPITAL)
    assert absolute == pytest.approx(300.0)
    assert pct == pytest.approx(300.0 / 100_500.0 * 100.0)


def test_drawdown_is_non_negative_even_when_pnl_is_always_negative():
    """The failure mode of ``(peak_pnl - trough_pnl) / peak_pnl``.

    With a peak PnL of -50 and a trough of -200 that formula returns -300%: a
    negative drawdown, which is not a quantity. On the equity curve the peak is
    bounded below by the capital base, so the result stays sane.
    """
    pct, absolute = max_drawdown([-50.0, -120.0, -200.0], CAPITAL)
    assert absolute == pytest.approx(150.0)
    assert pct is not None and pct > 0


def test_drawdown_of_a_monotonic_gain_is_zero():
    pct, absolute = max_drawdown([0.0, 10.0, 20.0, 30.0], CAPITAL)
    assert absolute == pytest.approx(0.0)
    assert pct == pytest.approx(0.0)


def test_sortino_only_penalises_downside():
    upside_only = [0.0, 100.0, 200.0, 300.0, 400.0]
    assert sortino_ratio(upside_only, CAPITAL, 0.0, 98_280) is None, (
        "with no negative returns there is no downside deviation to divide by"
    )

    mixed = [0.0, 100.0, -50.0, 200.0, 120.0, 300.0]
    sortino = sortino_ratio(mixed, CAPITAL, 0.0, 98_280)
    sharpe, _ = sharpe_ratio(mixed, CAPITAL, 0.0, 98_280)
    assert sortino is not None and sharpe is not None
    assert sortino > sharpe, (
        "a right-skewed series should score better on Sortino than on Sharpe, "
        "because Sharpe treats upside dispersion as risk"
    )


def test_annualised_volatility_scales_with_the_step_count():
    pnl = [0.0, 100.0, -50.0, 75.0]
    v1 = annualised_volatility(pnl, CAPITAL, 252)
    v2 = annualised_volatility(pnl, CAPITAL, 252 * 4)
    assert v2 == pytest.approx(v1 * 2.0)


def _summary(**overrides):
    kwargs = dict(
        pnl_series=[0.0, 10.0, -5.0, 20.0],
        inventory_series=[0.0, 5.0, -3.0, 10.0],
        realized_pnl=15.0,
        unrealized_pnl=5.0,
        fees_paid=0.0,
        win_rate=0.5,
        closed_round_trips=4,
        fill_count=8,
        capital_base=CAPITAL,
        risk_free_per_step=0.0,
        steps_per_year=98_280,
        max_inventory=100.0,
    )
    kwargs.update(overrides)
    return summarise(**kwargs)


def test_summary_reports_the_final_pnl_and_peak_inventory():
    s = _summary()
    assert s.total_pnl == pytest.approx(20.0)
    assert s.peak_inventory_abs == pytest.approx(10.0)
    assert s.final_inventory == pytest.approx(10.0)
    assert s.total_return_pct == pytest.approx(20.0 / CAPITAL * 100.0)


def test_summary_flags_an_inventory_breach():
    s = _summary(inventory_series=[0.0, 50.0, 130.0, 90.0], max_inventory=100.0)
    assert s.max_inventory_risk_score == pytest.approx(1.3)
    assert any("Inventory limit breached" in n for n in s.notes)


def test_summary_flags_a_short_sample_annualisation():
    s = _summary()
    assert any("Annualised Sharpe extrapolates" in n for n in s.notes), (
        "annualising a 4-step sample by sqrt(98280) has to be labelled"
    )


def test_summary_flags_an_agent_with_no_closed_trades():
    s = _summary(closed_round_trips=0, win_rate=None)
    assert any("No closed round trips" in n for n in s.notes)


def test_summary_serialises_to_json_safe_primitives():
    payload = _summary().as_dict()
    assert isinstance(payload["notes"], list)
    for key, value in payload.items():
        assert value is None or isinstance(value, (int, float, list)), key
