"""Implied volatility by bracketed Brent, not by Newton."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from typing import Any

from app.services.derivatives.pricing import (
    BlackScholesInputs,
    OptionType,
    black_scholes_price,
)

MIN_VOL = 1e-6
MAX_VOL = 10.0
DEFAULT_TOLERANCE = 1e-10
DEFAULT_MAX_ITERATIONS = 100
# How far the price must move across a *meaningful* change in volatility for the quote.
IDENTIFIABILITY_ULPS = 8.0
# How wide the set of volatilities consistent with a quote may be before the quote is called.
MAX_AMBIGUITY = 0.005


@dataclass(frozen=True, slots=True)
class ImpliedVolResult:
    volatility: float | None
    iterations: int
    # Set when ``volatility`` is None.
    reason: str | None = None
    bracket: tuple[float, float] | None = None

    @property
    def ok(self) -> bool:
        return self.volatility is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "volatility": self.volatility,
            "iterations": self.iterations,
            "reason": self.reason,
            "bracket": list(self.bracket) if self.bracket else None,
        }


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    tau: float,
    option_type: OptionType = "CALL",
    rate: float = 0.0,
    dividend: float = 0.0,
    tolerance: float = DEFAULT_TOLERANCE,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> ImpliedVolResult:
    """Invert Black-Scholes for volatility, or explain why it cannot be done."""
    if price < 0:
        return ImpliedVolResult(None, 0, "a negative option price is not a quote")
    if tau <= 0:
        return ImpliedVolResult(
            None, 0, "an expired option has no implied volatility: its price is intrinsic"
        )

    def model(sigma: float) -> float:
        return black_scholes_price(
            BlackScholesInputs(
                spot=spot,
                strike=strike,
                tau=tau,
                volatility=sigma,
                rate=rate,
                dividend=dividend,
                option_type=option_type,
            )
        )

    floor = model(MIN_VOL)
    if price < floor - tolerance:
        return ImpliedVolResult(
            None,
            0,
            f"price {price:.6f} is below the zero-volatility floor {floor:.6f}: "
            "the quote is arbitrageable against the forward",
        )

    # Double the upper bound until the model price exceeds the market price.
    high = 0.2
    iterations = 0
    while model(high) < price and high < MAX_VOL:
        high *= 2.0
        iterations += 1
    if model(high) < price:
        return ImpliedVolResult(
            None,
            iterations,
            f"price {price:.6f} exceeds the model price at {MAX_VOL:.0f}00% volatility: "
            "no volatility reproduces this quote",
        )

    low = MIN_VOL
    volatility, used = _brent(lambda s: model(s) - price, low, high, tolerance, max_iterations)
    if volatility is None:
        return ImpliedVolResult(
            None,
            used,
            f"the solver did not converge within {max_iterations} iterations",
            (low, high),
        )
    # Identifiability, asked the only way that answers it: *how many* volatilities reproduce.
    resolution = IDENTIFIABILITY_ULPS * sys.float_info.epsilon * max(abs(price), 1.0)
    consistent_low, consistent_high = _flat_interval(
        model, price, volatility, low, high, resolution
    )
    if consistent_high - consistent_low > MAX_AMBIGUITY:
        return ImpliedVolResult(
            None,
            used,
            f"every volatility between {consistent_low:.4f} and {consistent_high:.4f} "
            f"reproduces this price to within {resolution:.2e}, so the quote does not "
            "identify one (deep in the money: the price is intrinsic value and "
            "nothing else)",
            (low, high),
        )

    return ImpliedVolResult(volatility, used, None, (low, high))


def _flat_interval(
    model, price: float, root: float, low: float, high: float, resolution: float
) -> tuple[float, float]:
    """The range of volatilities whose model price equals ``price`` numerically."""

    def indistinguishable(sigma: float) -> bool:
        return abs(model(sigma) - price) <= resolution

    def edge(start: float, limit: float) -> float:
        if indistinguishable(limit):
            return limit
        near, far = start, limit
        for _ in range(60):
            middle = (near + far) / 2.0
            if indistinguishable(middle):
                near = middle
            else:
                far = middle
            if abs(far - near) < 1e-9:
                break
        return near

    if not indistinguishable(root):  # pragma: no cover - the root solves it
        return root, root
    return edge(root, low), edge(root, high)


def _brent(
    f, a: float, b: float, tolerance: float, max_iterations: int
) -> tuple[float | None, int]:
    """Brent's method on ``[a, b]``, which must already bracket a root."""
    fa, fb = f(a), f(b)
    if fa * fb > 0:  # pragma: no cover - the caller establishes the bracket
        return None, 0
    if abs(fa) < abs(fb):
        a, b, fa, fb = b, a, fb, fa

    c, fc = a, fa
    d = e = b - a
    for iteration in range(1, max_iterations + 1):
        if fb == 0.0 or abs(b - a) < tolerance:
            return b, iteration

        if fa != fc and fb != fc:
            # Inverse quadratic interpolation through the three points.
            s = (
                a * fb * fc / ((fa - fb) * (fa - fc))
                + b * fa * fc / ((fb - fa) * (fb - fc))
                + c * fa * fb / ((fc - fa) * (fc - fb))
            )
        else:
            s = b - fb * (b - a) / (fb - fa) if fb != fa else (a + b) / 2.0

        lower, upper = min((3 * a + b) / 4.0, b), max((3 * a + b) / 4.0, b)
        bisect = not (lower < s < upper) or abs(s - b) >= abs(e) / 2.0
        if bisect:
            s = (a + b) / 2.0
            e = d = b - a
        else:
            e, d = d, abs(b - s)

        fs = f(s)
        c, fc = b, fb
        if fa * fs < 0:
            b, fb = s, fs
        else:
            a, fa = s, fs
        if abs(fa) < abs(fb):
            a, b, fa, fb = b, a, fb, fa

    return (b, max_iterations) if abs(fb) < math.sqrt(tolerance) else (None, max_iterations)
