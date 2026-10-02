"""Microprice: a fair value that knows which way the next trade goes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


def weighted_microprice(
    best_bid: float | None,
    best_ask: float | None,
    bid_quantity: float,
    ask_quantity: float,
) -> float | None:
    """First-order microprice. ``None`` when there is no two-sided book."""
    if best_bid is None or best_ask is None:
        return None
    total = bid_quantity + ask_quantity
    if total <= 0:
        return (best_bid + best_ask) / 2.0
    imbalance = bid_quantity / total
    return imbalance * best_ask + (1.0 - imbalance) * best_bid


def imbalance(bid_quantity: float, ask_quantity: float) -> float:
    """Queue imbalance in [0, 1]. Exactly 0.5 when the two sides are equal."""
    total = bid_quantity + ask_quantity
    if total <= 0:
        return 0.5
    return bid_quantity / total


@dataclass
class MicropriceEstimator:
    """Stoikov's iterated conditional expectation, on a discretised state."""

    n_imbalance_buckets: int = 10
    max_spread_ticks: int = 10
    discount: float = 1.0
    iterations: int = 50
    tolerance: float = 1e-8
    min_observations: int = 20

    _counts: dict[tuple[int, int], int] = field(default_factory=dict, init=False)
    _mean_move: dict[tuple[int, int], float] = field(default_factory=dict, init=False)
    _transitions: dict[tuple[int, int], dict[tuple[int, int], int]] = field(
        default_factory=dict, init=False
    )
    _adjustment: dict[tuple[int, int], float] = field(default_factory=dict, init=False)
    _fitted: bool = field(default=False, init=False)

    # ------------------------------------------------------------------ state
    def state_of(self, imbalance_value: float, spread_ticks: int) -> tuple[int, int]:
        bucket = min(
            self.n_imbalance_buckets - 1,
            max(0, int(imbalance_value * self.n_imbalance_buckets)),
        )
        spread = min(max(int(spread_ticks), 1), self.max_spread_ticks)
        return bucket, spread

    def observe_series(
        self, observations: list[tuple[float, int, float]]
    ) -> "MicropriceEstimator":
        """Learn from ``(imbalance, spread_ticks, mid)`` samples, in order."""
        previous_state: tuple[int, int] | None = None
        previous_mid: float | None = None

        for imbalance_value, spread_ticks, mid in observations:
            state = self.state_of(imbalance_value, spread_ticks)
            if previous_state is not None and previous_mid is not None:
                if state != previous_state or mid != previous_mid:
                    move = mid - previous_mid
                    count = self._counts.get(previous_state, 0) + 1
                    self._counts[previous_state] = count
                    mean = self._mean_move.get(previous_state, 0.0)
                    self._mean_move[previous_state] = mean + (move - mean) / count
                    bucket = self._transitions.setdefault(previous_state, {})
                    bucket[state] = bucket.get(state, 0) + 1
                    previous_state, previous_mid = state, mid
            else:
                previous_state, previous_mid = state, mid
        return self

    def fit(self) -> "MicropriceEstimator":
        """Iterate the conditional expectation to convergence."""
        states = [s for s, count in self._counts.items() if count >= self.min_observations]
        if not states:
            self._fitted = False
            return self

        adjustment = {state: self._mean_move[state] for state in states}
        for _ in range(self.iterations):
            updated: dict[tuple[int, int], float] = {}
            delta = 0.0
            for state in states:
                total = sum(self._transitions.get(state, {}).values())
                expectation = 0.0
                if total:
                    for next_state, count in self._transitions[state].items():
                        expectation += (count / total) * adjustment.get(next_state, 0.0)
                value = self._mean_move[state] + self.discount * expectation
                updated[state] = value
                delta = max(delta, abs(value - adjustment[state]))
            adjustment = updated
            if delta < self.tolerance:
                break

        self._adjustment = adjustment
        self._fitted = True
        return self

    # ------------------------------------------------------------------ value
    def fair_value(
        self,
        best_bid: float | None,
        best_ask: float | None,
        bid_quantity: float,
        ask_quantity: float,
        tick_size: float,
    ) -> float | None:
        """Microprice for the current book, bounded by the touch."""
        if best_bid is None or best_ask is None:
            return None

        cold = weighted_microprice(best_bid, best_ask, bid_quantity, ask_quantity)
        if not self._fitted:
            return cold

        mid = (best_bid + best_ask) / 2.0
        spread_ticks = max(1, int(round((best_ask - best_bid) / tick_size)))
        state = self.state_of(imbalance(bid_quantity, ask_quantity), spread_ticks)
        adjustment = self._adjustment.get(state)
        if adjustment is None:
            return cold
        return float(min(max(mid + adjustment, best_bid), best_ask))

    @property
    def fitted(self) -> bool:
        return self._fitted

    def summary(self) -> dict[str, Any]:
        counts = np.array(list(self._counts.values()), dtype=float) if self._counts else np.zeros(0)
        return {
            "fitted": self._fitted,
            "states_observed": len(self._counts),
            "states_fitted": len(self._adjustment),
            "transitions": int(counts.sum()),
            "min_observations": self.min_observations,
        }
