"""Pricing, Greeks, implied volatility and the volatility surface."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.derivatives import (
    BlackScholesInputs,
    black_scholes_price,
    fit_svi_slice,
    greeks,
    implied_volatility,
    intrinsic_value,
    put_call_parity_gap,
)
from app.services.derivatives.svi import (
    SVIParams,
    SVISlice,
    butterfly_arbitrage,
    calendar_arbitrage,
    svi_total_variance,
)

GRID = [
    (spot, strike, tau, vol)
    for spot in (80.0, 100.0, 125.0)
    for strike in (70.0, 100.0, 130.0)
    for tau in (0.02, 0.25, 1.5)
    for vol in (0.08, 0.3, 0.9)
]


def _inputs(spot, strike, tau, vol, option_type="CALL", rate=0.03, dividend=0.01):
    return BlackScholesInputs(
        spot=spot,
        strike=strike,
        tau=tau,
        volatility=vol,
        rate=rate,
        dividend=dividend,
        option_type=option_type,
    )


# ------------------------------------------------------------------- pricing
@pytest.mark.parametrize("spot, strike, tau, vol", GRID)
def test_put_call_parity_holds_to_1e10(spot, strike, tau, vol):
    call = black_scholes_price(_inputs(spot, strike, tau, vol, "CALL"))
    put = black_scholes_price(_inputs(spot, strike, tau, vol, "PUT"))
    gap = put_call_parity_gap(call, put, spot, strike, tau, rate=0.03, dividend=0.01)
    assert abs(gap) < 1e-10


def test_a_known_textbook_price():
    """S=100, K=100, tau=1, sigma=20%, r=5%, q=0 -> 10.4506 (Hull)."""
    price = black_scholes_price(
        BlackScholesInputs(spot=100, strike=100, tau=1.0, volatility=0.2, rate=0.05)
    )
    assert price == pytest.approx(10.450583572185565, abs=1e-9)


@pytest.mark.parametrize("option_type", ["CALL", "PUT"])
def test_price_is_monotone_in_volatility(option_type):
    prices = [
        black_scholes_price(_inputs(100, 100, 0.5, vol, option_type))
        for vol in (0.05, 0.1, 0.2, 0.4, 0.8)
    ]
    assert prices == sorted(prices)


def test_price_never_falls_below_intrinsic():
    for spot, strike, tau, vol in GRID:
        for option_type in ("CALL", "PUT"):
            price = black_scholes_price(
                _inputs(spot, strike, tau, vol, option_type, rate=0.0, dividend=0.0)
            )
            assert price >= intrinsic_value(spot, strike, option_type) - 1e-9


def test_at_expiry_the_price_is_intrinsic_and_not_nan():
    """tau -> 0 divides by zero in d1. The guard is the point."""
    for tau in (0.0, 1e-15):
        price = black_scholes_price(_inputs(110, 100, tau, 0.3, "CALL"))
        assert math.isfinite(price)
        assert price == pytest.approx(10.0, abs=1e-6)
        assert black_scholes_price(_inputs(90, 100, tau, 0.3, "CALL")) == pytest.approx(0.0)


def test_zero_volatility_is_a_known_forward_not_a_nan():
    price = black_scholes_price(_inputs(100, 90, 1.0, 0.0, "CALL", rate=0.0, dividend=0.0))
    assert price == pytest.approx(10.0, abs=1e-9)


def test_deep_out_of_the_money_underflows_to_zero_cleanly():
    price = black_scholes_price(_inputs(100, 400, 0.05, 0.1, "CALL"))
    assert price == 0.0
    assert math.isfinite(price)


def test_invalid_inputs_are_rejected_at_the_boundary():
    with pytest.raises(ValueError, match="spot"):
        black_scholes_price(_inputs(0, 100, 1.0, 0.2))
    with pytest.raises(ValueError, match="tau cannot be negative"):
        black_scholes_price(_inputs(100, 100, -0.5, 0.2))
    with pytest.raises(ValueError, match="option_type"):
        black_scholes_price(
            BlackScholesInputs(spot=100, strike=100, tau=1, volatility=0.2, option_type="STRADDLE")
        )


# -------------------------------------------------------------------- Greeks
def _finite_difference(field: str, inputs: BlackScholesInputs, bump: float) -> float:
    kwargs = {
        "spot": inputs.spot,
        "strike": inputs.strike,
        "tau": inputs.tau,
        "volatility": inputs.volatility,
        "rate": inputs.rate,
        "dividend": inputs.dividend,
        "option_type": inputs.option_type,
    }
    up = dict(kwargs)
    down = dict(kwargs)
    up[field] = kwargs[field] + bump
    down[field] = kwargs[field] - bump
    return (
        black_scholes_price(BlackScholesInputs(**up))
        - black_scholes_price(BlackScholesInputs(**down))
    ) / (2 * bump)


@pytest.mark.parametrize("option_type", ["CALL", "PUT"])
@pytest.mark.parametrize("spot, strike, tau, vol", GRID[::4])
def test_analytic_greeks_match_central_finite_differences(option_type, spot, strike, tau, vol):
    inputs = _inputs(spot, strike, tau, vol, option_type)
    analytic = greeks(inputs)

    assert analytic.delta == pytest.approx(
        _finite_difference("spot", inputs, spot * 1e-5), abs=1e-5
    )
    assert analytic.vega == pytest.approx(
        _finite_difference("volatility", inputs, 1e-5), abs=1e-4
    )
    assert analytic.rho == pytest.approx(_finite_difference("rate", inputs, 1e-6), abs=1e-3)
    # Theta is minus the derivative with respect to tau: time runs forward.
    assert analytic.theta == pytest.approx(
        -_finite_difference("tau", inputs, min(1e-6, tau / 2)), abs=1e-3
    )


@pytest.mark.parametrize("spot, strike, tau, vol", GRID[::5])
def test_gamma_matches_the_second_difference_of_price(spot, strike, tau, vol):
    inputs = _inputs(spot, strike, tau, vol)
    bump = spot * 1e-3
    up = black_scholes_price(_inputs(spot + bump, strike, tau, vol))
    mid = black_scholes_price(inputs)
    down = black_scholes_price(_inputs(spot - bump, strike, tau, vol))
    numeric = (up - 2 * mid + down) / (bump**2)
    assert greeks(inputs).gamma == pytest.approx(numeric, abs=1e-4)


def test_gamma_and_vega_are_the_same_for_calls_and_puts():
    """Parity is linear in spot, so its curvature and vol sensitivity cancel."""
    call = greeks(_inputs(105, 100, 0.7, 0.25, "CALL"))
    put = greeks(_inputs(105, 100, 0.7, 0.25, "PUT"))
    assert call.gamma == pytest.approx(put.gamma, abs=1e-12)
    assert call.vega == pytest.approx(put.vega, abs=1e-12)
    assert call.delta - put.delta == pytest.approx(
        math.exp(-0.01 * 0.7), abs=1e-12
    ), "delta_call - delta_put = e^{-q tau}"


def test_delta_is_bounded_by_side():
    for spot, strike, tau, vol in GRID:
        assert 0.0 <= greeks(_inputs(spot, strike, tau, vol, "CALL")).delta <= 1.0
        assert -1.0 <= greeks(_inputs(spot, strike, tau, vol, "PUT")).delta <= 0.0


def test_at_expiry_delta_is_a_step_and_every_other_greek_is_zero():
    itm = greeks(_inputs(120, 100, 0.0, 0.3, "CALL"))
    assert itm.degenerate
    assert itm.delta == 1.0
    assert (itm.gamma, itm.vega, itm.theta, itm.rho) == (0.0, 0.0, 0.0, 0.0)

    otm = greeks(_inputs(80, 100, 0.0, 0.3, "CALL"))
    assert otm.delta == 0.0
    at_the_money = greeks(_inputs(100, 100, 0.0, 0.3, "CALL"))
    assert at_the_money.delta == 0.5, "undefined at the strike; 0.5 is the stated convention"


def test_quoted_conventions_are_carried_next_to_the_raw_values():
    result = greeks(_inputs(100, 100, 1.0, 0.25))
    assert result.vega_per_point == pytest.approx(result.vega * 0.01)
    assert result.theta_per_day == pytest.approx(result.theta / 365.0)
    assert "per calendar day" in result.as_dict()["conventions"]["theta_per_day"]


# --------------------------------------------------------- implied volatility
@pytest.mark.parametrize("option_type", ["CALL", "PUT"])
@pytest.mark.parametrize("spot, strike, tau, vol", GRID[::3])
def test_implied_vol_round_trips_across_the_grid(option_type, spot, strike, tau, vol):
    inputs = _inputs(spot, strike, tau, vol, option_type)
    price = black_scholes_price(inputs)
    if price < 1e-8:
        pytest.skip("a worthless option carries no volatility information")

    result = implied_volatility(
        price, spot, strike, tau, option_type, rate=0.03, dividend=0.01
    )

    if greeks(inputs).vega == 0.0:
        # Deep in the money the price is intrinsic and nothing else: vega underflows to zero, so no.
        assert not result.ok
        assert "does not identify one" in result.reason
        return

    assert result.ok, result.reason
    assert result.volatility == pytest.approx(vol, abs=1e-6)


def test_a_deep_in_the_money_quote_identifies_no_volatility():
    """Its price is intrinsic value; every sigma reproduces it bit for bit."""
    price = black_scholes_price(_inputs(100, 70, 0.25, 0.08, "CALL"))
    result = implied_volatility(price, 100, 70, 0.25, "CALL", rate=0.03, dividend=0.01)
    assert result.volatility is None
    assert "does not identify one" in result.reason


def test_a_price_below_the_no_arbitrage_floor_returns_none_with_a_reason():
    result = implied_volatility(0.5, 130.0, 100.0, 1.0, "CALL", rate=0.0)
    assert result.volatility is None
    assert "below the zero-volatility floor" in result.reason


def test_a_price_no_volatility_can_reach_returns_none_with_a_reason():
    result = implied_volatility(99.0, 100.0, 100.0, 0.1, "CALL")
    assert result.volatility is None
    assert "no volatility reproduces this quote" in result.reason


def test_an_expired_option_has_no_implied_volatility():
    result = implied_volatility(5.0, 100.0, 100.0, 0.0)
    assert result.volatility is None
    assert "expired" in result.reason


def test_a_negative_price_is_not_a_quote():
    assert implied_volatility(-1.0, 100, 100, 1.0).reason.startswith("a negative")


def test_the_solver_respects_its_iteration_cap():
    price = black_scholes_price(_inputs(100, 100, 1.0, 0.3))
    result = implied_volatility(price, 100, 100, 1.0, rate=0.03, dividend=0.01)
    assert result.iterations <= 100


def test_deep_wings_still_invert_where_newton_would_diverge():
    """Vega is tiny here - the reason this uses Brent on a bracket."""
    for strike in (40.0, 260.0):
        price = black_scholes_price(_inputs(100, strike, 0.05, 0.6, "CALL"))
        if price < 1e-10:
            continue
        result = implied_volatility(price, 100, strike, 0.05, "CALL", rate=0.03, dividend=0.01)
        assert result.ok, f"{strike}: {result.reason}"
        assert result.volatility == pytest.approx(0.6, abs=1e-4)


# ------------------------------------------------------------------ the smile
TRUTH = SVIParams(a=0.04, b=0.16, rho=-0.55, m=0.02, sigma=0.18)


def test_the_fit_recovers_known_parameters_from_clean_quotes():
    k = np.linspace(-0.45, 0.45, 15)
    w = svi_total_variance(k, TRUTH)

    fitted = fit_svi_slice(k, w, tau=0.5)
    assert fitted.rmse < 1e-3

    recovered = svi_total_variance(k, fitted.params)
    assert np.allclose(recovered, w, atol=2e-3)


def test_the_fit_is_close_under_noise_and_reports_its_rmse():
    rng = np.random.default_rng(4)
    k = np.linspace(-0.4, 0.4, 21)
    w = svi_total_variance(k, TRUTH) + rng.normal(0, 5e-4, k.size)

    fitted = fit_svi_slice(k, w, tau=0.5)
    assert fitted.rmse < 5e-3
    assert len(fitted.quotes_k) == 21, "the quotes travel with the fit for the residual plot"


def test_a_slice_needs_at_least_five_quotes():
    with pytest.raises(ValueError, match="five quotes"):
        fit_svi_slice([0.0, 0.1], [0.04, 0.05], tau=0.5)


def test_the_butterfly_check_fires_on_a_deliberately_bad_slice():
    """Huge b with rho at the edge bends the smile past positive density."""
    bad = SVIParams(a=0.001, b=2.5, rho=-0.999, m=0.0, sigma=0.01)
    violations = butterfly_arbitrage(bad, (-0.5, 0.5))
    assert violations, "this slice implies a negative density and must be reported"
    assert violations[0].kind == "butterfly"
    assert violations[0].magnitude > 0
    assert "negative" in violations[0].detail


def test_a_sane_slice_passes_the_butterfly_check():
    assert butterfly_arbitrage(TRUTH, (-0.5, 0.5)) == []


def test_the_calendar_check_fires_when_total_variance_falls_with_maturity():
    near = SVISlice(tau=0.25, params=SVIParams(0.05, 0.1, -0.2, 0.0, 0.2), rmse=0.0, butterfly_ok=True)
    far = SVISlice(tau=1.0, params=SVIParams(0.02, 0.1, -0.2, 0.0, 0.2), rmse=0.0, butterfly_ok=True)

    violations = calendar_arbitrage([near, far])
    assert violations, "a far slice below a near one is negative forward variance"
    assert violations[0].kind == "calendar"
    assert "negative forward variance" in violations[0].detail


def test_a_well_ordered_surface_passes_the_calendar_check():
    near = SVISlice(tau=0.25, params=SVIParams(0.02, 0.1, -0.2, 0.0, 0.2), rmse=0.0, butterfly_ok=True)
    far = SVISlice(tau=1.0, params=SVIParams(0.06, 0.12, -0.2, 0.0, 0.2), rmse=0.0, butterfly_ok=True)
    assert calendar_arbitrage([near, far]) == []
