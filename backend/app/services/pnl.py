"""Position and PnL accounting.

Realized PnL is computed by FIFO lot matching, not by the shortcut in most
student implementations (``sum(sells) - sum(buys)``), which is not realized PnL
at all - it is cash flow, and it reports a fictional loss the moment an agent is
net long, because the inventory it paid for has not been sold yet.

The case that makes lot matching non-trivial is a **position flip**: an agent
long 10 sells 25 in one fill. That fill closes 10 long at a realized profit and
*opens* 15 short at the fill price. Handling it as one signed number silently
books the opening 15 as if it had closed something.

Fees are charged per fill on notional. Defaults are zero so the zero-sum
identity (all agents' realized + unrealized PnL sums to zero) holds exactly in
the test suite; with fees on, the sum is negative by exactly the fees collected,
which is also checked.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.enums import Side

_EPS = 1e-9


@dataclass(slots=True)
class _Lot:
    """An open parcel of inventory. ``quantity`` is signed: + long, - short."""

    quantity: float
    price: float


@dataclass(slots=True)
class RoundTrip:
    """One closed parcel. The unit that win rate is computed over."""

    quantity: float
    entry_price: float
    exit_price: float
    pnl: float
    step: int


@dataclass
class PositionTracker:
    """Inventory, realized/unrealized PnL, and round-trip history for one agent."""

    agent_id: str
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0

    inventory: float = 0.0
    realized_pnl: float = 0.0
    fees_paid: float = 0.0
    gross_traded_notional: float = 0.0
    fill_count: int = 0
    _lots: deque[_Lot] = field(default_factory=deque)
    round_trips: list[RoundTrip] = field(default_factory=list)

    # ------------------------------------------------------------------ fills
    def apply_fill(
        self, side: Side, quantity: float, price: float, is_maker: bool, step: int = 0
    ) -> None:
        if quantity <= 0:
            raise ValueError("fill quantity must be positive")

        fee_bps = self.maker_fee_bps if is_maker else self.taker_fee_bps
        notional = quantity * price
        fee = notional * fee_bps / 10_000.0
        self.fees_paid += fee
        self.realized_pnl -= fee
        self.gross_traded_notional += notional
        self.fill_count += 1

        signed = quantity * side.sign
        remaining = signed

        # Phase 1: consume opposing lots, oldest first.
        while abs(remaining) > _EPS and self._lots and _opposes(self._lots[0].quantity, remaining):
            lot = self._lots[0]
            closed = min(abs(lot.quantity), abs(remaining))
            # A long lot realizes (exit - entry); a short lot realizes the
            # negative of that. lot_direction carries the sign.
            lot_direction = 1.0 if lot.quantity > 0 else -1.0
            pnl = (price - lot.price) * closed * lot_direction
            self.realized_pnl += pnl
            self.round_trips.append(
                RoundTrip(
                    quantity=closed,
                    entry_price=lot.price,
                    exit_price=price,
                    pnl=pnl,
                    step=step,
                )
            )

            lot.quantity -= closed * lot_direction
            if abs(lot.quantity) <= _EPS:
                self._lots.popleft()
            remaining += closed * lot_direction

        # Phase 2: whatever is left opens new inventory (this is the flip case).
        if abs(remaining) > _EPS:
            if self._lots and not _opposes(self._lots[-1].quantity, remaining):
                pass  # same-sign lots stay separate so FIFO order is preserved
            self._lots.append(_Lot(quantity=remaining, price=price))

        self.inventory += signed
        if abs(self.inventory) <= _EPS:
            self.inventory = 0.0

    # ----------------------------------------------------------------- marks
    @property
    def average_entry_price(self) -> float | None:
        """Quantity-weighted average price of the currently open lots."""
        total = sum(abs(lot.quantity) for lot in self._lots)
        if total <= _EPS:
            return None
        return sum(abs(lot.quantity) * lot.price for lot in self._lots) / total

    def unrealized_pnl(self, mark_price: float | None) -> float:
        """Mark-to-market on open inventory.

        ``None`` mark (an empty side of the book) marks at zero rather than
        guessing, and the run summary records that the mark was unavailable.
        """
        if mark_price is None or self.inventory == 0.0:
            return 0.0
        avg = self.average_entry_price
        if avg is None:
            return 0.0
        return (mark_price - avg) * self.inventory

    def total_pnl(self, mark_price: float | None) -> float:
        return self.realized_pnl + self.unrealized_pnl(mark_price)

    # ------------------------------------------------------------------- risk
    def inventory_risk_score(self, max_inventory: float) -> float:
        if max_inventory <= 0:
            raise ValueError("max_inventory must be positive")
        return abs(self.inventory) / max_inventory

    def open_lot_count(self) -> int:
        return len(self._lots)

    # ---------------------------------------------------------------- stats
    @property
    def closed_round_trips(self) -> int:
        return len(self.round_trips)

    @property
    def winning_round_trips(self) -> int:
        return sum(1 for rt in self.round_trips if rt.pnl > 0)

    @property
    def win_rate(self) -> float | None:
        """Profitable closed round-trips / closed round-trips.

        ``None`` rather than 0.0 when nothing has closed: an agent that has not
        completed a round trip has no win rate, and reporting 0% invites the
        reader to conclude it lost every trade.
        """
        if not self.round_trips:
            return None
        return self.winning_round_trips / len(self.round_trips)


def _opposes(a: float, b: float) -> bool:
    return (a > 0 > b) or (a < 0 < b)
