"""Agent interface.

An agent is a pure function of what it can observe. It returns *intents*; it
never touches the book, the tracker, or the database. That separation is what
makes each strategy testable in isolation - every agent test in this repo
constructs a MarketView by hand and asserts on the intents, with no simulation
running.

What an agent may observe is deliberately restricted:

- the current book (touch and depth),
- ``price_history``: the end-of-step mid price series for every *completed*
  step, which is public information any participant could read off the book,
- ``trade_prices``: the executed trade tape,
- its own inventory and PnL.

Signals are computed from ``price_history``, not from ``trade_prices``. That is
not a cosmetic choice - the first version of this engine keyed the directional
signals off the trade tape and deadlocked: the tape is empty until someone
trades, and nobody trades until a signal fires. Real strategies read the quoted
price series, and the quoted series exists from the first step a maker quotes.

``reference_price`` - the latent GBM level - is read by the **market maker only**,
as its fair value. That is a stated simplification, not an accident: a live maker
infers fair value from order flow, and handing it the true fundamental gives it a
small informational edge over the directional agents. Building an inference model
instead is a project of its own and would not change the microstructure mechanics
this platform exists to demonstrate. A directional agent that read
``reference_price`` would be look-ahead bias, and there is a test
(``test_no_lookahead.py``) that perturbs the field and asserts their decisions do
not move.
"""

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
    #: Latent fundamental. Market maker only - see the module docstring.
    reference_price: float
    #: End-of-step mid prices for completed steps. Strictly historical: it has
    #: ``step - 1`` entries, so nothing in it can encode this step's outcome.
    price_history: Sequence[float]
    #: The executed tape. Available for analysis; signals use price_history.
    trade_prices: Sequence[float]
    inventory: float
    realized_pnl: float
    unrealized_pnl: float
    tick_size: float

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

    #: Parameter name -> default. Subclasses declare their full parameter set
    #: here so that ``resolve_config`` can validate an incoming config strictly
    #: instead of silently ignoring a misspelled key - a typo'd "threshhold"
    #: that leaves the default in place is a result you cannot reproduce.
    DEFAULTS: ClassVar[dict[str, Any]] = {}
    #: Whether this archetype re-quotes (cancel + replace) every step.
    REQUOTES_EACH_STEP: ClassVar[bool] = False

    #: Set by the engine when an inventory breach forces a liquidation.
    flags: list[str] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        # Declared on the base rather than on each subclass on purpose: a
        # dataclass only emits the ``__post_init__`` call into ``__init__`` if
        # the hook exists on the class being decorated. A subclass that defines
        # ``__post_init__`` while the decorated base does not would never have
        # it called, and the missing attribute would surface as an
        # AttributeError on the first decide() rather than at construction.
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

    # ------------------------------------------------------------------ helpers
    def _clamp_to_inventory_limit(
        self, side: Side, quantity: float, inventory: float
    ) -> float:
        """Shrink an order so filling it cannot breach the inventory limit.

        Position limits that are only checked *after* a fill are not limits. The
        engine still runs a post-fill breach check and forces liquidation,
        because a resting order can fill several steps after it was sized - but
        an agent that knowingly sends an order that would breach its own limit
        is a bug, not a strategy.
        """
        if _same_direction(side, inventory):
            room = self.max_inventory - abs(inventory)
        else:
            # Trading against the position: there is room to flatten it *and*
            # then to build the same limit in the other direction.
            room = self.max_inventory + abs(inventory)
        return max(0.0, min(quantity, room))


def _same_direction(side: Side, inventory: float) -> bool:
    return (side is Side.BUY and inventory >= 0) or (side is Side.SELL and inventory <= 0)
