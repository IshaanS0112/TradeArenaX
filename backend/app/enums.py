"""Domain enumerations.

Declared as ``str`` subclasses so they serialise to plain strings in JSON and
compare equal to the VARCHAR values stored in Postgres without a cast.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """``enum.StrEnum`` semantics without requiring Python 3.11.

    ``enum.StrEnum`` landed in 3.11. Mixing in ``str`` and pinning ``__str__`` to
    the value is exactly what it does, and doing it here keeps the package
    importable on 3.10 - still the system interpreter on Ubuntu 22.04, and
    therefore on a lot of CI images. Without the ``__str__`` override, 3.10 would
    render ``str(Side.BUY)`` as ``"Side.BUY"`` and quietly write that string into
    the database.
    """

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
    # A MARKET order is executed as immediate-or-cancel against whatever is
    # resting. It is never added to the book: an unfilled remainder is dropped,
    # not queued. Only used by forced liquidation and by directional agents that
    # are configured to cross the spread.
    MARKET = "MARKET"


class OrderStatus(StrEnum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    # A MARKET order whose remainder could not be filled. Distinguished from
    # CANCELLED so the run log can tell "the agent pulled the order" apart from
    # "the book had no liquidity", which are different diagnoses.
    EXPIRED = "EXPIRED"


class AgentType(StrEnum):
    MARKET_MAKER = "MARKET_MAKER"
    MOMENTUM = "MOMENTUM"
    MEAN_REVERSION = "MEAN_REVERSION"


class SelfTradePrevention(StrEnum):
    """What to do when an agent's incoming order would match its own resting order.

    A market maker quotes both sides of the book, so as soon as its own spread
    inverts - which a volatility shock will do - it can trade with itself and
    book a fictional profit. Real venues forbid this; so does this engine.
    """

    CANCEL_RESTING = "CANCEL_RESTING"  # cancel the resting order, keep matching
    CANCEL_INCOMING = "CANCEL_INCOMING"  # drop the aggressing order
    SKIP = "SKIP"  # step over the resting order, match the next one behind it
