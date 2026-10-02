"""Black-Scholes, from first principles, with the edge cases that break."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

OptionType = Literal["CALL", "PUT"]

# Below this, N(x) is zero to double precision and the option is worthless.
_UNDERFLOW = -8.0
# Time and volatility below these are treated as expiry / certainty.
_MIN_TAU = 1e-12
_MIN_SIGMA = 1e-12

# Trading conventions for the quoted Greeks.
DAYS_PER_YEAR = 365.0
VOL_POINT = 0.01


def _norm_cdf(x: float) -> float:
    if x < _UNDERFLOW:
        return 0.0
    if x > -_UNDERFLOW:
        return 1.0
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _norm_pdf(x: float) -> float:
    if abs(x) > -_UNDERFLOW:
        return 0.0
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True, slots=True)
class BlackScholesInputs:
    spot: float
    strike: float
    # Time to expiry in years.
    tau: float
    volatility: float
    rate: float = 0.0
    dividend: float = 0.0
    option_type: OptionType = "CALL"

    def validate(self) -> None:
        if self.spot <= 0:
            raise ValueError("spot must be positive")
        if self.strike <= 0:
            raise ValueError("strike must be positive")
        if self.tau < 0:
            raise ValueError("tau cannot be negative: an option cannot expire backwards")
        if self.volatility < 0:
            raise ValueError("volatility cannot be negative")
        if self.option_type not in ("CALL", "PUT"):
            raise ValueError(f"option_type must be CALL or PUT, got {self.option_type}")


def intrinsic_value(spot: float, strike: float, option_type: OptionType) -> float:
    return max(spot - strike, 0.0) if option_type == "CALL" else max(strike - spot, 0.0)


def _d1_d2(inputs: BlackScholesInputs) -> tuple[float, float]:
    vol_sqrt_tau = inputs.volatility * math.sqrt(inputs.tau)
    d1 = (
        math.log(inputs.spot / inputs.strike)
        + (inputs.rate - inputs.dividend + 0.5 * inputs.volatility**2) * inputs.tau
    ) / vol_sqrt_tau
    return d1, d1 - vol_sqrt_tau


def _is_degenerate(inputs: BlackScholesInputs) -> bool:
    """No time left, or no uncertainty left. Both make d1 undefined."""
    return inputs.tau <= _MIN_TAU or inputs.volatility <= _MIN_SIGMA


def black_scholes_price(inputs: BlackScholesInputs) -> float:
    inputs.validate()

    if _is_degenerate(inputs):
        # With no volatility the forward is known, so the payoff is discounted intrinsic value.
        forward = inputs.spot * math.exp((inputs.rate - inputs.dividend) * inputs.tau)
        return math.exp(-inputs.rate * inputs.tau) * intrinsic_value(
            forward, inputs.strike, inputs.option_type
        )

    d1, d2 = _d1_d2(inputs)
    discount = math.exp(-inputs.rate * inputs.tau)
    carry = math.exp(-inputs.dividend * inputs.tau)

    if inputs.option_type == "CALL":
        return inputs.spot * carry * _norm_cdf(d1) - inputs.strike * discount * _norm_cdf(d2)
    return inputs.strike * discount * _norm_cdf(-d2) - inputs.spot * carry * _norm_cdf(-d1)


@dataclass(frozen=True, slots=True)
class Greeks:
    price: float
    delta: float
    gamma: float
    # Per 1.00 of volatility (raw) and per volatility point (quoted).
    vega: float
    vega_per_point: float
    # Per year (raw) and per calendar day (quoted).
    theta: float
    theta_per_day: float
    rho: float
    vanna: float
    vomma: float
    # True when the position was priced at expiry or at zero volatility, where every Greek but delta.
    degenerate: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "vega_per_point": self.vega_per_point,
            "theta": self.theta,
            "theta_per_day": self.theta_per_day,
            "rho": self.rho,
            "vanna": self.vanna,
            "vomma": self.vomma,
            "degenerate": self.degenerate,
            "conventions": {
                "vega_per_point": f"per {VOL_POINT:.0%} of volatility",
                "theta_per_day": f"per calendar day ({DAYS_PER_YEAR:.0f}/year)",
            },
        }


def greeks(inputs: BlackScholesInputs) -> Greeks:
    """Every Greek analytically. No finite differences - those are the test."""
    inputs.validate()
    price = black_scholes_price(inputs)

    if _is_degenerate(inputs):
        # At expiry delta is a step function: one if in the money, zero if out, and at the strike.
        forward = inputs.spot * math.exp((inputs.rate - inputs.dividend) * inputs.tau)
        if forward > inputs.strike:
            delta = 1.0 if inputs.option_type == "CALL" else 0.0
        elif forward < inputs.strike:
            delta = 0.0 if inputs.option_type == "CALL" else -1.0
        else:
            delta = 0.5 if inputs.option_type == "CALL" else -0.5
        return Greeks(
            price=price,
            delta=delta,
            gamma=0.0,
            vega=0.0,
            vega_per_point=0.0,
            theta=0.0,
            theta_per_day=0.0,
            rho=0.0,
            vanna=0.0,
            vomma=0.0,
            degenerate=True,
        )

    d1, d2 = _d1_d2(inputs)
    sqrt_tau = math.sqrt(inputs.tau)
    discount = math.exp(-inputs.rate * inputs.tau)
    carry = math.exp(-inputs.dividend * inputs.tau)
    pdf = _norm_pdf(d1)

    if inputs.option_type == "CALL":
        delta = carry * _norm_cdf(d1)
        theta = (
            -inputs.spot * pdf * inputs.volatility * carry / (2 * sqrt_tau)
            - inputs.rate * inputs.strike * discount * _norm_cdf(d2)
            + inputs.dividend * inputs.spot * carry * _norm_cdf(d1)
        )
        rho = inputs.strike * inputs.tau * discount * _norm_cdf(d2)
    else:
        delta = -carry * _norm_cdf(-d1)
        theta = (
            -inputs.spot * pdf * inputs.volatility * carry / (2 * sqrt_tau)
            + inputs.rate * inputs.strike * discount * _norm_cdf(-d2)
            - inputs.dividend * inputs.spot * carry * _norm_cdf(-d1)
        )
        rho = -inputs.strike * inputs.tau * discount * _norm_cdf(-d2)

    # Gamma and vega are identical for calls and puts - put-call parity is linear in the spot.
    gamma = carry * pdf / (inputs.spot * inputs.volatility * sqrt_tau)
    vega = inputs.spot * carry * pdf * sqrt_tau
    vanna = -carry * pdf * d2 / inputs.volatility
    vomma = vega * d1 * d2 / inputs.volatility

    return Greeks(
        price=price,
        delta=delta,
        gamma=gamma,
        vega=vega,
        vega_per_point=vega * VOL_POINT,
        theta=theta,
        theta_per_day=theta / DAYS_PER_YEAR,
        rho=rho,
        vanna=vanna,
        vomma=vomma,
    )


def put_call_parity_gap(
    call: float, put: float, spot: float, strike: float, tau: float, rate: float = 0.0,
    dividend: float = 0.0,
) -> float:
    """``C - P - (S e^{-q tau} - K e^{-r tau})``, which must be zero."""
    return call - put - (spot * math.exp(-dividend * tau) - strike * math.exp(-rate * tau))
