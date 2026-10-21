"""Per-agent latency."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# How much simulated time one step spans.
STEP_DURATION_US = 1_000_000

_ALLOWED_KEYS = frozenset({"latency_in_us", "latency_out_us", "latency_jitter_us"})


@dataclass(frozen=True, slots=True)
class LatencyProfile:
    """One agent's connection. All fields are non-negative microseconds."""

    latency_in_us: int = 0
    latency_out_us: int = 0
    latency_jitter_us: float = 0.0

    @property
    def is_zero(self) -> bool:
        """True when this agent behaves exactly as V1 did: no delay at all."""
        return (
            self.latency_in_us == 0
            and self.latency_out_us == 0
            and self.latency_jitter_us == 0.0
        )

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "LatencyProfile":
        """Build from a stored ``agents.latency_config`` blob."""
        if not config:
            return cls()

        unknown = sorted(set(config) - _ALLOWED_KEYS)
        if unknown:
            raise ValueError(
                f"unknown latency config key(s): {', '.join(unknown)}. "
                f"Valid keys are: {', '.join(sorted(_ALLOWED_KEYS))}"
            )

        values: dict[str, Any] = {}
        for key in ("latency_in_us", "latency_out_us"):
            if key in config:
                value = int(config[key])
                if value < 0:
                    raise ValueError(f"{key} must be >= 0, got {value}")
                values[key] = value
        if "latency_jitter_us" in config:
            jitter = float(config["latency_jitter_us"])
            if jitter < 0:
                raise ValueError(f"latency_jitter_us must be >= 0, got {jitter}")
            values["latency_jitter_us"] = jitter

        return cls(**values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "latency_in_us": self.latency_in_us,
            "latency_out_us": self.latency_out_us,
            "latency_jitter_us": self.latency_jitter_us,
        }

    def sample_in(self, rng: np.random.Generator) -> int:
        return self._sample(self.latency_in_us, rng)

    def sample_out(self, rng: np.random.Generator) -> int:
        return self._sample(self.latency_out_us, rng)

    def _sample(self, base: int, rng: np.random.Generator) -> int:
        """Base delay plus clamped Gaussian jitter, in whole microseconds."""
        if self.latency_jitter_us <= 0.0:
            return base
        drawn = base + rng.normal(0.0, self.latency_jitter_us)
        return int(max(0.0, drawn))
