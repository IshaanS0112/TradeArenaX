"""Inventory-aware market maker (simplified Avellaneda-Stoikov).

    fair_value = reference price
    skew       = k * inventory
    bid        = fair_value - spread/2 - skew
    ask        = fair_value + spread/2 - skew

The skew subtracts from *both* quotes, which is the part worth being able to
explain out loud. Long inventory pushes both prices down: the bid gets less
attractive to sellers, the ask gets more attractive to buyers, so the flow the
maker attracts is biased toward reducing the position. It does not widen the
spread - the width stays constant - it *shifts the centre of the quote away from
the risk*. Widening the spread would be a response to volatility, not to
inventory, and the two get conflated constantly.

The full Avellaneda-Stoikov result derives both the reservation price shift and
the optimal half-spread from a utility function with a risk-aversion parameter
and a finite horizon. This uses the linear inventory term and a fixed width,
which is the standard simplification: it reproduces the qualitative behaviour
(quotes lean against inventory) without the closed-form machinery. That is stated
in docs/architecture.md, not claimed as the full model.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.config import get_settings
from app.enums import OrderType, Side
from app.services.agents.base import Agent, MarketView, OrderIntent


class MarketMakerAgent(Agent):
    REQUOTES_EACH_STEP: ClassVar[bool] = True

    _s = get_settings()
    DEFAULTS: ClassVar[dict[str, Any]] = {
        "spread": _s.mm_spread,
        "quote_size": _s.mm_quote_size,
        "inventory_skew_k": _s.mm_inventory_skew_k,
        "max_inventory": _s.mm_max_inventory,
        # Above this fraction of the limit the maker stops adding to the losing
        # side entirely. Skew alone is a price incentive; at the limit you need
        # a hard stop, because a sufficiently one-directional market will pay
        # the skew and keep filling you.
        "one_sided_at_risk_score": 0.85,
        # Widen the quote in proportion to recent realised volatility. A fixed
        # width during a shock is how a market maker gets run over: every quote
        # is stale by the time it is hit.
        "volatility_widening": True,
        "volatility_window": 20,
        "volatility_widening_k": 2.0,
        "max_spread_multiple": 6.0,
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if config["spread"] <= 0:
            raise ValueError("spread must be positive, or the maker crosses itself")
        if config["quote_size"] <= 0:
            raise ValueError("quote_size must be positive")
        if config["inventory_skew_k"] < 0:
            raise ValueError(
                "inventory_skew_k must be non-negative: a negative k skews quotes "
                "*toward* the existing position, which accelerates the blow-up"
            )
        if not 0 < config["one_sided_at_risk_score"] <= 1.0:
            raise ValueError("one_sided_at_risk_score must be in (0, 1]")
        if config["max_spread_multiple"] < 1.0:
            raise ValueError("max_spread_multiple must be >= 1.0")

    # ------------------------------------------------------------------ quoting
    def half_spread(self, view: MarketView) -> float:
        base = float(self.config["spread"]) / 2.0
        if not self.config["volatility_widening"]:
            return base

        window = int(self.config["volatility_window"])
        prices = view.price_history[-window:]
        if len(prices) < 3:
            return base

        mean = sum(prices) / len(prices)
        var = sum((p - mean) ** 2 for p in prices) / (len(prices) - 1)
        sd = var**0.5
        widened = base + float(self.config["volatility_widening_k"]) * sd
        return min(widened, base * float(self.config["max_spread_multiple"]))

    def skew(self, inventory: float) -> float:
        return float(self.config["inventory_skew_k"]) * inventory

    def decide(self, view: MarketView) -> list[OrderIntent]:
        fair_value = view.reference_price
        half = self.half_spread(view)
        skew = self.skew(view.inventory)

        bid_price = fair_value - half - skew
        ask_price = fair_value + half - skew

        size = float(self.config["quote_size"])
        risk_score = abs(view.inventory) / self.max_inventory
        cutoff = float(self.config["one_sided_at_risk_score"])

        intents: list[OrderIntent] = []

        quote_bid = not (view.inventory > 0 and risk_score >= cutoff)
        quote_ask = not (view.inventory < 0 and risk_score >= cutoff)

        if quote_bid and bid_price > 0:
            qty = self._clamp_to_inventory_limit(Side.BUY, size, view.inventory)
            if qty > 0:
                intents.append(
                    OrderIntent(
                        side=Side.BUY,
                        quantity=qty,
                        price=bid_price,
                        order_type=OrderType.LIMIT,
                        reason=f"quote bid: fv={fair_value:.4f} half={half:.4f} skew={skew:.4f}",
                    )
                )

        if quote_ask and ask_price > 0:
            qty = self._clamp_to_inventory_limit(Side.SELL, size, view.inventory)
            if qty > 0:
                intents.append(
                    OrderIntent(
                        side=Side.SELL,
                        quantity=qty,
                        price=ask_price,
                        order_type=OrderType.LIMIT,
                        reason=f"quote ask: fv={fair_value:.4f} half={half:.4f} skew={skew:.4f}",
                    )
                )

        return intents
