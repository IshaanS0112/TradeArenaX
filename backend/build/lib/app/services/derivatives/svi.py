"""SVI: a volatility smile that is not allowed to be arbitrageable."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class SVIParams:
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        return (self.a, self.b, self.rho, self.m, self.sigma)

    def as_dict(self) -> dict[str, float]:
        return {"a": self.a, "b": self.b, "rho": self.rho, "m": self.m, "sigma": self.sigma}

    def validate(self) -> None:
        if self.b < 0:
            raise ValueError("b must be non-negative")
        if not -1.0 <= self.rho <= 1.0:
            raise ValueError("rho must be in [-1, 1]")
        if self.sigma <= 0:
            raise ValueError("sigma must be positive")


def svi_total_variance(k: float | np.ndarray, params: SVIParams) -> float | np.ndarray:
    """Total implied variance at log-moneyness ``k``."""
    k_array = np.asarray(k, dtype=float)
    value = params.a + params.b * (
        params.rho * (k_array - params.m)
        + np.sqrt((k_array - params.m) ** 2 + params.sigma**2)
    )
    return float(value) if np.isscalar(k) or value.ndim == 0 else value


def implied_vol_from_slice(k: float, params: SVIParams, tau: float) -> float | None:
    """Convert total variance back to a volatility. None if the slice is negative."""
    if tau <= 0:
        return None
    w = svi_total_variance(k, params)
    if w <= 0:
        return None
    return math.sqrt(w / tau)


@dataclass(slots=True)
class ArbitrageViolation:
    kind: str  # "butterfly" | "calendar"
    k: float
    magnitude: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "k": self.k,
            "magnitude": self.magnitude,
            "detail": self.detail,
        }


@dataclass(slots=True)
class SVISlice:
    tau: float
    params: SVIParams
    rmse: float
    butterfly_ok: bool
    violations: list[ArbitrageViolation] = field(default_factory=list)
    # The quotes it was fitted to, kept so the residuals can be plotted.
    quotes_k: list[float] = field(default_factory=list)
    quotes_w: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tau": self.tau,
            "params": self.params.as_dict(),
            "rmse": self.rmse,
            "butterfly_ok": self.butterfly_ok,
            "violations": [v.as_dict() for v in self.violations],
            "quotes_k": self.quotes_k,
            "quotes_w": self.quotes_w,
        }


def butterfly_g(k: float, params: SVIParams) -> float:
    """Gatheral's ``g(k)``; negative anywhere means negative density."""
    w = float(svi_total_variance(k, params))
    if w <= 0:
        return -1.0

    h = 1e-4
    w_plus = float(svi_total_variance(k + h, params))
    w_minus = float(svi_total_variance(k - h, params))
    dw = (w_plus - w_minus) / (2 * h)
    d2w = (w_plus - 2 * w + w_minus) / (h * h)

    return (
        (1 - k * dw / (2 * w)) ** 2
        - (dw**2 / 4) * (1 / w + 0.25)
        + d2w / 2
    )


def butterfly_arbitrage(
    params: SVIParams, k_range: tuple[float, float] = (-1.0, 1.0), samples: int = 201
) -> list[ArbitrageViolation]:
    """Scan the slice for negative density."""
    violations: list[ArbitrageViolation] = []
    for k in np.linspace(k_range[0], k_range[1], samples):
        g = butterfly_g(float(k), params)
        if g < 0:
            violations.append(
                ArbitrageViolation(
                    kind="butterfly",
                    k=float(k),
                    magnitude=float(-g),
                    detail=f"g(k) = {g:.6f} < 0: the implied density is negative here",
                )
            )
    return violations


def calendar_arbitrage(
    slices: Sequence[SVISlice], k_range: tuple[float, float] = (-1.0, 1.0), samples: int = 101
) -> list[ArbitrageViolation]:
    """Total variance must be non-decreasing in maturity, at every k."""
    ordered = sorted(slices, key=lambda s: s.tau)
    violations: list[ArbitrageViolation] = []

    for near, far in zip(ordered, ordered[1:], strict=False):
        for k in np.linspace(k_range[0], k_range[1], samples):
            near_w = float(svi_total_variance(float(k), near.params))
            far_w = float(svi_total_variance(float(k), far.params))
            if far_w < near_w - 1e-12:
                violations.append(
                    ArbitrageViolation(
                        kind="calendar",
                        k=float(k),
                        magnitude=float(near_w - far_w),
                        detail=(
                            f"total variance falls from {near_w:.6f} at tau={near.tau:.4f} "
                            f"to {far_w:.6f} at tau={far.tau:.4f}: negative forward variance"
                        ),
                    )
                )
    return violations


def fit_svi_slice(
    log_moneyness: Sequence[float],
    total_variance: Sequence[float],
    tau: float,
    restarts: int = 6,
    seed: int = 11,
) -> SVISlice:
    """Least-squares fit of one slice, with arbitrage reported rather than hidden."""
    k = np.asarray(list(log_moneyness), dtype=float)
    w = np.asarray(list(total_variance), dtype=float)
    if k.size != w.size:
        raise ValueError("log_moneyness and total_variance must be the same length")
    if k.size < 5:
        raise ValueError("an SVI slice has five parameters: fit it to at least five quotes")

    def objective(params: SVIParams) -> float:
        model = svi_total_variance(k, params)
        return float(np.mean((model - w) ** 2))

    rng = np.random.default_rng(seed)
    best: SVIParams | None = None
    best_error = math.inf

    for restart in range(restarts):
        if restart == 0:
            candidate = SVIParams(
                a=float(max(w.min(), 1e-6)),
                b=0.1,
                rho=-0.3,
                m=float(k[int(np.argmin(w))]),
                sigma=0.2,
            )
        else:
            candidate = SVIParams(
                a=float(max(w.min() * rng.uniform(0.2, 1.2), 1e-6)),
                b=float(rng.uniform(0.01, 0.6)),
                rho=float(rng.uniform(-0.9, 0.9)),
                m=float(rng.uniform(k.min(), k.max())),
                sigma=float(rng.uniform(0.05, 0.8)),
            )

        current, error = _descend(candidate, objective)
        if error < best_error:
            best, best_error = current, error

    assert best is not None
    violations = butterfly_arbitrage(best, (float(k.min()) - 0.2, float(k.max()) + 0.2))

    return SVISlice(
        tau=tau,
        params=best,
        rmse=math.sqrt(best_error),
        butterfly_ok=not violations,
        violations=violations,
        quotes_k=[float(x) for x in k],
        quotes_w=[float(x) for x in w],
    )


def _descend(start: SVIParams, objective) -> tuple[SVIParams, float]:
    """Coordinate descent with a shrinking step, respecting the constraints."""
    names = ("a", "b", "rho", "m", "sigma")
    bounds = {
        "a": (-1.0, 5.0),
        "b": (0.0, 5.0),
        "rho": (-0.999, 0.999),
        "m": (-3.0, 3.0),
        "sigma": (1e-4, 5.0),
    }
    current = dict(zip(names, start.as_tuple(), strict=True))
    best_error = objective(SVIParams(**current))
    step = {"a": 0.05, "b": 0.05, "rho": 0.1, "m": 0.1, "sigma": 0.1}

    for _ in range(300):
        improved = False
        for name in names:
            for direction in (1.0, -1.0):
                low, high = bounds[name]
                trial = dict(current)
                trial[name] = min(max(current[name] + direction * step[name], low), high)
                error = objective(SVIParams(**trial))
                if error < best_error - 1e-15:
                    current, best_error, improved = trial, error, True
                    break
        if not improved:
            if all(s < 1e-6 for s in step.values()):
                break
            step = {name: value / 2.0 for name, value in step.items()}

    return SVIParams(**current), best_error
