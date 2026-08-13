"""Mean-reversion agent.

    z = (P_t - moving_average) / moving_std_dev
    z >  threshold  ->  SELL  (price rich to its recent mean)
    z < -threshold  ->  BUY

The sign is the entire difference from the momentum agent, and it is worth being
able to state precisely: *momentum trades with the deviation, mean reversion
trades against it.* Both read the same price series; they disagree about whether
a move is information or noise. Under a GBM with zero drift the mean-reversion
agent has the better prior, because GBM log returns are independent - there is no
trend to follow. That is a property of the price process, not evidence that one
strategy is better, and the README says so rather than letting the comparison
table imply otherwise.

Guards that matter:

- **Zero or near-zero moving std** produces an infinite z-score. A flat book
  makes this the common case, not the edge case, so a dispersion floor is
  enforced in ticks and no signal is emitted below it.
- **Sample std needs a sample.** With ``window`` observations the estimator uses
  ``ddof=1``; below three observations there is no signal at all.
"""

from __future__ import annotations

import statistics
from typing import Any, ClassVar

from app.config import get_settings
from app.enums import OrderType, Side
from app.services.agents.base import Agent, MarketView, OrderIntent


class MeanReversionAgent(Agent):
    _s = get_settings()
    DEFAULTS: ClassVar[dict[str, Any]] = {
        "window": _s.reversion_window,
        "z_threshold": _s.reversion_z_threshold,
        "base_size": _s.reversion_base_size,
        "max_size": _s.reversion_max_size,
        "max_inventory": _s.reversion_max_inventory,
        "size_gain": 1.0,
        "aggressive_order_type": OrderType.MARKET.value,
        "cooldown_steps": 3,
        # Minimum moving std, expressed in ticks, before a z-score is trusted.
        "min_dispersion_ticks": 1.0,
        # Flatten when the deviation is worked off. Without an exit rule the
        # agent accumulates one-directional inventory across a trending path
        # and the strategy becomes "buy the dip, forever".
        "exit_z": 0.25,
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if config["window"] < 3:
            raise ValueError("window must be at least 3 to estimate a sample std dev")
        if config["z_threshold"] <= 0:
            raise ValueError("z_threshold must be positive")
        if config["base_size"] <= 0 or config["max_size"] <= 0:
            raise ValueError("sizes must be positive")
        if config["max_size"] < config["base_size"]:
            raise ValueError("max_size must be >= base_size")
        if config["min_dispersion_ticks"] <= 0:
            raise ValueError(
                "min_dispersion_ticks must be positive: it is the guard against "
                "dividing by a zero moving std dev"
            )
        if not 0 <= config["exit_z"] < config["z_threshold"]:
            raise ValueError("exit_z must be in [0, z_threshold)")
        if config["aggressive_order_type"] not in (OrderType.MARKET, OrderType.LIMIT):
            raise ValueError("aggressive_order_type must be MARKET or LIMIT")

    def z_score(self, view: MarketView) -> float | None:
        window = int(self.config["window"])
        prices = list(view.price_history[-window:])
        if len(prices) < 3:
            return None
        mean = statistics.fmean(prices)
        sd = statistics.stdev(prices)
        floor = float(self.config["min_dispersion_ticks"]) * view.tick_size
        if sd < floor:
            return None
        return (float(prices[-1]) - mean) / sd

    def _size_for(self, z: float) -> float:
        threshold = float(self.config["z_threshold"])
        excess = abs(z) / threshold - 1.0
        size = float(self.config["base_size"]) * (
            1.0 + float(self.config["size_gain"]) * max(0.0, excess)
        )
        return min(size, float(self.config["max_size"]))

    def decide(self, view: MarketView) -> list[OrderIntent]:
        z = self.z_score(view)
        if z is None:
            return []

        order_type = OrderType(self.config["aggressive_order_type"])

        # Exit first: a position held through a reverted deviation is no longer
        # a mean-reversion trade, it is a directional bet the agent never made.
        if abs(z) <= float(self.config["exit_z"]) and view.inventory != 0:
            side = Side.SELL if view.inventory > 0 else Side.BUY
            price = None
            if order_type is OrderType.LIMIT:
                price = view.best_bid if side is Side.SELL else view.best_ask
                if price is None:
                    return []
            return [
                OrderIntent(
                    side=side,
                    quantity=abs(view.inventory),
                    price=price,
                    order_type=order_type,
                    reason=f"exit: z {z:+.3f} back inside exit band",
                )
            ]

        cooldown = int(self.config["cooldown_steps"])
        if self._last_trade_step is not None and view.step - self._last_trade_step < cooldown:
            return []

        threshold = float(self.config["z_threshold"])
        if abs(z) < threshold:
            return []

        # Trade *against* the deviation - this sign is the whole strategy.
        side = Side.SELL if z > 0 else Side.BUY
        qty = self._clamp_to_inventory_limit(side, self._size_for(z), view.inventory)
        if qty <= 0:
            return []

        price = None
        if order_type is OrderType.LIMIT:
            price = view.best_bid if side is Side.SELL else view.best_ask
            if price is None:
                return []

        self._last_trade_step = view.step
        return [
            OrderIntent(
                side=side,
                quantity=qty,
                price=price,
                order_type=order_type,
                reason=f"z {z:+.3f} beyond threshold {threshold:.2f}, trading against it",
            )
        ]
