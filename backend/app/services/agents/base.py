"""Agent interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Sequence

from app.enums import OrderType, Side


@dataclass(frozen=True)
class MarketView:
    """Everything an agent is allowed to know at a given step."""

    step: int
    best_bid: float | None
    best_ask: float | None
    mid_price: float | None
    # : Latent fundamental.
    reference_price: float
    # : End-of-step mid prices for completed steps.
    price_history: Sequence[float]
    # : The executed tape.
    trade_prices: Sequence[float]
    inventory: float
    realized_pnl: float
    unrealized_pnl: float
    tick_size: float
    # : Resting quantity at the touch.
    bid_quantity: float = 0.0
    ask_quantity: float = 0.0

    @property
    def has_two_sided_book(self) -> bool:
        return self.best_bid is not None and self.best_ask is not None

    @property
    def mark_price(self) -> float:
        """Best available price for valuation: mid, else last observed, else tape."""
        if self.mid_price is not None:
            return self.mid_price
        if self.price_history:
            return float(self.price_history[-1])
        if self.trade_prices:
            return float(self.trade_prices[-1])
        return self.reference_price


@dataclass(frozen=True)
class OrderIntent:
    side: Side
    quantity: float
    price: float | None = None
    order_type: OrderType = OrderType.LIMIT
    reason: str = ""

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("intent quantity must be positive")
        if self.order_type is OrderType.LIMIT and self.price is None:
            raise ValueError("a LIMIT intent requires a price")


@dataclass
class Agent(ABC):
    agent_id: str
    config: dict[str, Any]

    # : Parameter name -> default.
    DEFAULTS: ClassVar[dict[str, Any]] = {}
    # : Whether this archetype re-quotes (cancel + replace) every step.
    REQUOTES_EACH_STEP: ClassVar[bool] = False

    # : Set by the engine when an inventory breach forces a liquidation.
    flags: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        # Declared on the base rather than on each subclass on purpose:.
        self._last_trade_step: int | None = None

    @classmethod
    def resolve_config(cls, config: dict[str, Any] | None) -> dict[str, Any]:
        config = dict(config or {})
        unknown = set(config) - set(cls.DEFAULTS)
        if unknown:
            raise ValueError(
                f"{cls.__name__} received unknown config keys: {sorted(unknown)}. "
                f"Known keys: {sorted(cls.DEFAULTS)}"
            )
        resolved = {**cls.DEFAULTS, **config}
        cls.validate_config(resolved)
        return resolved

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        if config.get("max_inventory", 1) <= 0:
            raise ValueError("max_inventory must be positive")

    @property
    def max_inventory(self) -> float:
        return float(self.config["max_inventory"])

    @abstractmethod
    def decide(self, view: MarketView) -> list[OrderIntent]:
        """Return the orders this agent wants to place at this step."""

    def _clamp_to_inventory_limit(
        self, side: Side, quantity: float, inventory: float
    ) -> float:
        """Shrink an order so filling it cannot breach the inventory limit."""
        if _same_direction(side, inventory):
            room = self.max_inventory - abs(inventory)
        else:
            # Trading against the position: there is room to flatten it *and*.
            room = self.max_inventory + abs(inventory)
        return max(0.0, min(quantity, room))


def _same_direction(side: Side, inventory: float) -> bool:
    return (side is Side.BUY and inventory >= 0) or (side is Side.SELL and inventory <= 0)
