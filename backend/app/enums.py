"""Domain enumerations."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """``enum.StrEnum`` semantics without requiring Python 3.11."""

    def __str__(self) -> str:
        return str(self.value)


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> "Side":
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        """+1 for a buy, -1 for a sell. Used to signed-sum inventory."""
        return 1 if self is Side.BUY else -1


class OrderType(StrEnum):
    LIMIT = "LIMIT"
    # A MARKET order is executed as immediate-or-cancel against whatever.
    MARKET = "MARKET"


class OrderStatus(StrEnum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    # A MARKET order whose remainder could not be filled.
    EXPIRED = "EXPIRED"


class AgentType(StrEnum):
    MARKET_MAKER = "MARKET_MAKER"
    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"
    # : Uninformed flow.
    NOISE_TRADER = "NOISE_TRADER"
    # : Quotes a strip of options and hedges the delta into this same equity.
    OPTIONS_MAKER = "OPTIONS_MAKER"


class SelfTradePrevention(StrEnum):
    """What to do when an agent's incoming order would match its own resting order."""

    CANCEL_RESTING = "CANCEL_RESTING"  # cancel the resting order, keep matching
    CANCEL_INCOMING = "CANCEL_INCOMING"  # drop the aggressing order
    SKIP = "SKIP"  # step over the resting order, match the next one behind it
