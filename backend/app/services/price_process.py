"""Reference price process: Geometric Brownian Motion with volatility shocks.

This is the one part of the system that is openly synthetic, and it is the part
whose synthetic-ness is standard practice: strategy research runs on generated
or historical paths, never on live capital.

Discretisation
--------------
The exact solution of ``dS = mu*S*dt + sigma*S*dW`` is used, not an Euler step:

    S_{t+1} = S_t * exp( (mu - sigma^2 / 2) * dt + sigma * sqrt(dt) * Z )

Euler (``S_{t+1} = S_t * (1 + mu*dt + sigma*sqrt(dt)*Z)``) can produce a
negative price whenever ``sigma*sqrt(dt)*Z < -1``, which for a 30% vol asset at
a one-minute step needs a ~200-sigma draw - so it looks safe, right up until
someone runs a stress scenario at 300% vol and a daily step and the price goes
through zero. The log form cannot: an exponential is positive.

Shocks
------
A shock is a jump discontinuity applied at a configured step: the price is
multiplied by ``(1 + magnitude_pct/100)`` on top of that step's diffusion. Real
crashes also leave elevated volatility behind them, so each shock optionally
raises sigma by a multiplier that decays back to baseline with a half-life. That
is volatility clustering, which is what makes a shock a stress test rather than
a single bad tick the agents forget about immediately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class VolatilityShock:
    step: int
    magnitude_pct: float
    # Multiplier applied to sigma from this step onward, decaying back to 1.0.
    vol_multiplier: float = 1.0
    vol_half_life_steps: int = 50

    def __post_init__(self) -> None:
        if self.step < 0:
            raise ValueError("shock step must be non-negative")
        if self.magnitude_pct <= -100.0:
            raise ValueError("a shock of -100% or worse would zero the price")
        if self.vol_multiplier < 1.0:
            raise ValueError("vol_multiplier must be >= 1.0 (a shock cannot calm a market)")
        if self.vol_half_life_steps <= 0:
            raise ValueError("vol_half_life_steps must be positive")


@dataclass
class PriceProcess:
    """Stateful GBM path generator. One instance per simulation run."""

    initial_price: float
    drift: float  # mu, annualised
    volatility: float  # sigma, annualised
    dt: float
    seed: int = 42
    shocks: tuple[VolatilityShock, ...] = ()

    _price: float = field(init=False)
    _step: int = field(init=False, default=0)
    _rng: np.random.Generator = field(init=False)
    _shocks_by_step: dict[int, VolatilityShock] = field(init=False)
    _active: list[tuple[VolatilityShock, int]] = field(init=False, default_factory=list)
    history: list[float] = field(init=False)

    def __post_init__(self) -> None:
        if self.initial_price <= 0:
            raise ValueError("initial_price must be positive")
        if self.volatility < 0:
            raise ValueError("volatility must be non-negative")
        if self.dt <= 0:
            raise ValueError("dt must be positive")
        self._price = float(self.initial_price)
        # A named seed rather than global np.random: two runs with the same
        # config must produce the same path, or nothing measured here is
        # reproducible and every comparison between agents is confounded by
        # a different random draw.
        self._rng = np.random.default_rng(self.seed)
        self._shocks_by_step = {}
        for s in self.shocks:
            if s.step in self._shocks_by_step:
                raise ValueError(f"two shocks configured at step {s.step}")
            self._shocks_by_step[s.step] = s
        self.history = [self._price]

    @property
    def price(self) -> float:
        return self._price

    @property
    def step_index(self) -> int:
        return self._step

    def current_volatility(self) -> float:
        """Baseline sigma scaled by every still-decaying shock."""
        sigma = self.volatility
        for shock, fired_at in self._active:
            elapsed = self._step - fired_at
            decay = 0.5 ** (elapsed / shock.vol_half_life_steps)
            sigma *= 1.0 + (shock.vol_multiplier - 1.0) * decay
        return sigma

    def advance(self) -> float:
        """Advance one step and return the new reference price."""
        self._step += 1

        shock = self._shocks_by_step.get(self._step)
        if shock is not None and shock.vol_multiplier > 1.0:
            self._active.append((shock, self._step))

        sigma = self.current_volatility()
        z = float(self._rng.standard_normal())
        log_return = (self.drift - 0.5 * sigma**2) * self.dt + sigma * math.sqrt(self.dt) * z
        self._price *= math.exp(log_return)

        if shock is not None:
            self._price *= 1.0 + shock.magnitude_pct / 100.0

        # The log form guarantees a mathematically positive price, but it does not
        # guarantee a *representable* one. At a large sigma the -sigma^2/2 drift
        # term dominates (at sigma=8, dt=0.05 it is -1.6 per step), so log(S)
        # marches toward -inf and after a few thousand steps exp() underflows to
        # exactly 0.0. Every downstream division and log then silently produces
        # inf or nan. Failing loudly with the parameters in the message is more
        # useful than a run full of nan metrics.
        if not math.isfinite(self._price) or self._price <= 0.0:
            raise ValueError(
                f"price process degenerated at step {self._step}: price is "
                f"{self._price!r}. With sigma={self.volatility} and dt={self.dt} the "
                f"-sigma^2/2 log-drift is {-0.5 * self.volatility**2 * self.dt:.4g} "
                "per step, so the path underflows to zero. Lower the volatility or "
                "the step size."
            )

        # Drop shocks whose vol contribution has decayed below a tick of
        # relevance, so a long run does not carry an ever-growing list.
        self._active = [
            (s, t) for s, t in self._active if (self._step - t) < 20 * s.vol_half_life_steps
        ]

        self.history.append(self._price)
        return self._price

    def realized_volatility(self, annualise: bool = True) -> float | None:
        """Sample volatility of the realised log returns of the path so far.

        Reported next to the configured sigma in the run summary. When a shock
        has fired these two numbers should disagree - if they do not, the shock
        did not do anything.
        """
        if len(self.history) < 3:
            return None
        arr = np.asarray(self.history, dtype=float)
        rets = np.diff(np.log(arr))
        sd = float(np.std(rets, ddof=1))
        return sd / math.sqrt(self.dt) if annualise else sd
