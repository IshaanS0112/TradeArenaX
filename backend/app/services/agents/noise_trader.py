"""A trader with no opinion."""

from __future__ import annotations

import zlib
from typing import Any, ClassVar

import numpy as np

from app.enums import OrderType, Side
from app.services.agents.base import Agent, MarketView, OrderIntent


class NoiseTraderAgent(Agent):
    DEFAULTS: ClassVar[dict[str, Any]] = {
        # Probability of acting at all on a given step.
        "activity": 0.35,
        "order_size": 5.0,
        # Offset from the mid, in ticks, drawn uniformly from [-offset_ticks, +offset_ticks].
        "offset_ticks": 3,
        "max_inventory": 120.0,
        # Its own RNG stream, so the flow is reproducible and independent of the price path's draws.
        "random_seed": 7,
    }

    def __post_init__(self) -> None:
        super().__post_init__()
        # Seeded per agent id as well as per config, so two noise traders with identical parameters.
        seed = int(self.config["random_seed"]) + (zlib.crc32(self.agent_id.encode()) % 10_000)
        self._rng = np.random.default_rng(seed)

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if not 0.0 <= float(config["activity"]) <= 1.0:
            raise ValueError("activity must be a probability in [0, 1]")
        if float(config["order_size"]) <= 0:
            raise ValueError("order_size must be positive")
        if int(config["offset_ticks"]) < 0:
            raise ValueError("offset_ticks must be >= 0")

    def decide(self, view: MarketView) -> list[OrderIntent]:
        if self._rng.random() > float(self.config["activity"]):
            return []
        # Anchor: the mid when there is one, else the last observed price, else the last trade, else.
        anchor = view.mark_price
        if anchor <= 0:  # pragma: no cover - a price process cannot go negative
            return []

        side = Side.BUY if self._rng.random() < 0.5 else Side.SELL
        offset_ticks = int(self.config["offset_ticks"])
        offset = int(self._rng.integers(-offset_ticks, offset_ticks + 1))
        sign = 1 if side is Side.BUY else -1
        price = anchor + sign * offset * view.tick_size

        quantity = self._clamp_to_inventory_limit(
            side, float(self.config["order_size"]), view.inventory
        )
        if quantity <= 0:
            return []

        return [
            OrderIntent(
                side=side,
                quantity=quantity,
                price=round(price, 10),
                order_type=OrderType.LIMIT,
                reason="noise",
            )
        ]
