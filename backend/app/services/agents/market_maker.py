"""Inventory-aware market maker (simplified Avellaneda-Stoikov)."""

from __future__ import annotations

from typing import Any, ClassVar

from app.config import get_settings
from app.enums import OrderType, Side
from app.services.agents.base import Agent, MarketView, OrderIntent
from app.services.microprice import weighted_microprice


class MarketMakerAgent(Agent):
    REQUOTES_EACH_STEP: ClassVar[bool] = True

    _s = get_settings()
    DEFAULTS: ClassVar[dict[str, Any]] = {
        "spread": _s.mm_spread,
        "quote_size": _s.mm_quote_size,
        "inventory_skew_k": _s.mm_inventory_skew_k,
        "max_inventory": _s.mm_max_inventory,
        # Above this fraction of the limit the maker stops adding to the losing.
        "one_sided_at_risk_score": 0.85,
        # Widen the quote in proportion to recent realised volatility.
        "volatility_widening": True,
        "volatility_window": 20,
        "volatility_widening_k": 2.0,
        "max_spread_multiple": 6.0,
        # Fair value: "reference" is the V1 behaviour (the latent GBM level).
        "fair_value_source": "reference",
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
        if config["fair_value_source"] not in ("reference", "microprice", "mid"):
            raise ValueError(
                "fair_value_source must be one of: reference, microprice, mid"
            )

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

    def fair_value(self, view: MarketView) -> float:
        """What this maker believes the instrument is worth right now."""
        source = self.config["fair_value_source"]
        if source == "reference":
            return view.reference_price
        if source == "mid":
            return view.mid_price if view.mid_price is not None else view.reference_price

        micro = weighted_microprice(
            view.best_bid, view.best_ask, view.bid_quantity, view.ask_quantity
        )
        return micro if micro is not None else view.reference_price

    def decide(self, view: MarketView) -> list[OrderIntent]:
        fair_value = self.fair_value(view)
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
