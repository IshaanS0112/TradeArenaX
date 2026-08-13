"""Momentum (trend-following) agent.

    price_change = (P_t - P_{t-N}) / P_{t-N}
    price_change >  threshold  ->  BUY,  size scaled by trend strength
    price_change < -threshold  ->  SELL

Two properties worth naming, because they are what an interviewer will probe:

**It trades the traded price, not the reference price.** The signal is computed
from the sequence of executed trade prices - what this agent could actually have
observed - rather than from the GBM path. An agent that reads the fundamental
directly is not a strategy, it is a look-ahead bug.

**It takes liquidity.** A trend follower that posts passively is asking the
market to come to it in the direction it thinks the market is leaving. So the
intents cross the spread: it pays the offer to buy. That is the structural reason
momentum and market making end up on opposite sides of the same fill, and the
reason the momentum agent pays the taker fee while the maker earns the spread.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.config import get_settings
from app.enums import OrderType, Side
from app.services.agents.base import Agent, MarketView, OrderIntent


class MomentumAgent(Agent):
    _s = get_settings()
    DEFAULTS: ClassVar[dict[str, Any]] = {
        "lookback": _s.momentum_lookback,
        "threshold": _s.momentum_threshold,
        "base_size": _s.momentum_base_size,
        "max_size": _s.momentum_max_size,
        "max_inventory": _s.momentum_max_inventory,
        # How hard size scales with signal strength. size = base * (1 + g * excess)
        # where excess is (|signal| / threshold - 1), so a signal exactly at the
        # threshold trades base size and conviction is earned, not assumed.
        "size_gain": 1.0,
        # Cross the spread (MARKET) or post an aggressive limit at the touch.
        # MARKET guarantees the fill and the slippage; the limit caps the price
        # paid and risks not trading at all.
        "aggressive_order_type": OrderType.MARKET.value,
        # Do not fire again for this many steps after a trade. Without it the
        # agent re-fires on the same trend every step and its position is a
        # function of how long the trend lasted rather than of its own sizing.
        "cooldown_steps": 3,
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if config["lookback"] < 1:
            raise ValueError("lookback must be at least 1")
        if config["threshold"] <= 0:
            raise ValueError(
                "threshold must be positive: a zero threshold fires on every "
                "non-zero tick and the agent becomes a random-noise trader"
            )
        if config["base_size"] <= 0 or config["max_size"] <= 0:
            raise ValueError("sizes must be positive")
        if config["max_size"] < config["base_size"]:
            raise ValueError("max_size must be >= base_size")
        if config["size_gain"] < 0:
            raise ValueError("size_gain must be non-negative")
        if config["cooldown_steps"] < 0:
            raise ValueError("cooldown_steps must be non-negative")
        if config["aggressive_order_type"] not in (OrderType.MARKET, OrderType.LIMIT):
            raise ValueError("aggressive_order_type must be MARKET or LIMIT")

    def signal(self, view: MarketView) -> float | None:
        """Return the lookback return, or ``None`` if there is not enough history."""
        lookback = int(self.config["lookback"])
        prices = view.price_history
        if len(prices) <= lookback:
            return None
        past = float(prices[-1 - lookback])
        if past <= 0:
            return None
        return (float(prices[-1]) - past) / past

    def _size_for(self, signal: float) -> float:
        threshold = float(self.config["threshold"])
        excess = abs(signal) / threshold - 1.0
        size = float(self.config["base_size"]) * (
            1.0 + float(self.config["size_gain"]) * max(0.0, excess)
        )
        return min(size, float(self.config["max_size"]))

    def decide(self, view: MarketView) -> list[OrderIntent]:
        cooldown = int(self.config["cooldown_steps"])
        if self._last_trade_step is not None and view.step - self._last_trade_step < cooldown:
            return []

        signal = self.signal(view)
        if signal is None:
            return []
        threshold = float(self.config["threshold"])
        if abs(signal) < threshold:
            return []

        side = Side.BUY if signal > 0 else Side.SELL
        qty = self._clamp_to_inventory_limit(side, self._size_for(signal), view.inventory)
        if qty <= 0:
            return []

        order_type = OrderType(self.config["aggressive_order_type"])
        price = None
        if order_type is OrderType.LIMIT:
            # Aggressive limit: pay up to the far touch, no further.
            price = view.best_ask if side is Side.BUY else view.best_bid
            if price is None:
                return []

        self._last_trade_step = view.step
        return [
            OrderIntent(
                side=side,
                quantity=qty,
                price=price,
                order_type=order_type,
                reason=(
                    f"momentum {signal:+.5f} over {self.config['lookback']} steps "
                    f"vs threshold {threshold:.5f}"
                ),
            )
        ]
