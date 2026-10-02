"""An options market maker that hedges into the equity book."""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass, field
from typing import Any, ClassVar

import numpy as np

from app.enums import OrderType, Side
from app.services.agents.base import Agent, MarketView, OrderIntent
from app.services.derivatives.pricing import BlackScholesInputs, Greeks, greeks


@dataclass(frozen=True, slots=True)
class OptionContract:
    """One line of the strip. Strikes are set relative to the opening spot."""

    strike: float
    expiry_step: int
    option_type: str  # CALL | PUT

    @property
    def key(self) -> str:
        return f"{self.option_type}-{self.strike:.4f}-{self.expiry_step}"


@dataclass(slots=True)
class GreeksSnapshot:
    """One step of the options book, in the units a desk would quote."""

    step: int
    spot: float
    portfolio_delta: float
    portfolio_gamma: float
    portfolio_vega: float
    portfolio_theta: float
    # The attribution.
    gamma_pnl: float
    vega_pnl: float
    theta_pnl: float
    hedge_slippage: float
    option_premium: float
    hedge_trades: int
    hedge_band: float
    net_delta_after_hedge: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "spot": self.spot,
            "portfolio_delta": self.portfolio_delta,
            "portfolio_gamma": self.portfolio_gamma,
            "portfolio_vega": self.portfolio_vega,
            "portfolio_theta": self.portfolio_theta,
            "gamma_pnl": self.gamma_pnl,
            "vega_pnl": self.vega_pnl,
            "theta_pnl": self.theta_pnl,
            "hedge_slippage": self.hedge_slippage,
            "option_premium": self.option_premium,
            "hedge_trades": self.hedge_trades,
            "hedge_band": self.hedge_band,
            "net_delta_after_hedge": self.net_delta_after_hedge,
        }


class OptionsMarketMakerAgent(Agent):
    """Quotes options, hedges the delta with equity orders."""

    REQUOTES_EACH_STEP: ClassVar[bool] = False

    DEFAULTS: ClassVar[dict[str, Any]] = {
        # Strikes as fractions of the opening spot: 0.95 is a 5% down strike.
        "strike_offsets": (0.95, 1.0, 1.05),
        "expiry_steps": 500,
        "contracts_per_quote": 1.0,
        # Contract multiplier: one option covers this many shares.
        "contract_multiplier": 10.0,
        # The volatility the maker quotes.
        "implied_volatility": 0.30,
        "risk_free_rate": 0.0,
        # Half-spread scaling.
        "vega_spread_k": 0.25,
        "gamma_spread_k": 0.5,
        # Probability that a client trades a given contract on a given step.
        "client_intensity": 0.12,
        # No-trade band, in shares of delta.
        "hedge_band": 25.0,
        # Used for the Whalley-Wilmott band when `hedge_band_mode` is "auto".
        "hedge_band_mode": "fixed",
        "transaction_cost": 0.01,
        "risk_aversion": 0.1,
        "max_inventory": 500.0,
        "steps_per_year": 98_280.0,
        "random_seed": 5,
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if not config["strike_offsets"]:
            raise ValueError("an options maker needs at least one strike")
        if float(config["implied_volatility"]) <= 0:
            raise ValueError("implied_volatility must be positive")
        if float(config["hedge_band"]) < 0:
            raise ValueError("hedge_band cannot be negative")
        if config["hedge_band_mode"] not in ("fixed", "auto"):
            raise ValueError("hedge_band_mode must be 'fixed' or 'auto'")
        if int(config["expiry_steps"]) <= 0:
            raise ValueError("expiry_steps must be positive")
        if not 0.0 <= float(config["client_intensity"]) <= 1.0:
            raise ValueError("client_intensity is a probability in [0, 1]")

    def __post_init__(self) -> None:
        super().__post_init__()
        seed = int(self.config["random_seed"]) + (
            zlib.crc32(self.agent_id.encode()) % 10_000
        )
        self._rng = np.random.default_rng(seed)
        self._contracts: list[OptionContract] = []
        # contract key -> signed position, in contracts.
        self.positions: dict[str, float] = {}
        self.snapshots: list[GreeksSnapshot] = []
        self.option_premium: float = 0.0
        self._last_spot: float | None = None
        self._last_greeks: Greeks | None = None
        self._hedge_trades_this_step = 0
        self._pending_hedge: float = 0.0

    # ----------------------------------------------------------------- strip
    def _ensure_strip(self, spot: float, step: int) -> None:
        if self._contracts:
            return
        expiry = step + int(self.config["expiry_steps"])
        for offset in self.config["strike_offsets"]:
            strike = round(spot * float(offset), 4)
            for option_type in ("CALL", "PUT"):
                self._contracts.append(
                    OptionContract(strike=strike, expiry_step=expiry, option_type=option_type)
                )

    def _tau(self, contract: OptionContract, step: int) -> float:
        remaining = max(contract.expiry_step - step, 0)
        return remaining / float(self.config["steps_per_year"])

    def _greeks_for(self, contract: OptionContract, spot: float, step: int) -> Greeks:
        return greeks(
            BlackScholesInputs(
                spot=spot,
                strike=contract.strike,
                tau=self._tau(contract, step),
                volatility=float(self.config["implied_volatility"]),
                rate=float(self.config["risk_free_rate"]),
                option_type=contract.option_type,  # type: ignore[arg-type]
            )
        )

    def quote(self, contract: OptionContract, spot: float, step: int) -> tuple[float, float]:
        """Bid and ask for one contract, widened by vega and gamma."""
        g = self._greeks_for(contract, spot, step)
        half = (
            float(self.config["vega_spread_k"]) * g.vega_per_point
            + float(self.config["gamma_spread_k"]) * g.gamma * spot * 0.01
        )
        half = max(half, 0.01)
        return max(g.price - half, 0.0), g.price + half

    # ----------------------------------------------------------- client flow
    def _client_flow(self, spot: float, step: int) -> float:
        """Simulate uninformed clients hitting the strip. Returns premium taken."""
        intensity = float(self.config["client_intensity"])
        size = float(self.config["contracts_per_quote"])
        premium = 0.0

        for contract in self._contracts:
            if self._rng.random() > intensity:
                continue
            bid, ask = self.quote(contract, spot, step)
            client_buys = self._rng.random() < 0.5
            if client_buys:
                # The client lifts the offer: the maker is short the option and receives the ask.
                self.positions[contract.key] = self.positions.get(contract.key, 0.0) - size
                premium += ask * size * float(self.config["contract_multiplier"])
            else:
                self.positions[contract.key] = self.positions.get(contract.key, 0.0) + size
                premium -= bid * size * float(self.config["contract_multiplier"])
        return premium

    # -------------------------------------------------------------- the book
    def portfolio_greeks(self, spot: float, step: int) -> Greeks:
        """Position-weighted Greeks of the whole strip, in share terms."""
        multiplier = float(self.config["contract_multiplier"])
        totals = {"price": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0,
                  "rho": 0.0, "vanna": 0.0, "vomma": 0.0}

        for contract in self._contracts:
            position = self.positions.get(contract.key, 0.0)
            if position == 0.0:
                continue
            g = self._greeks_for(contract, spot, step)
            weight = position * multiplier
            totals["price"] += g.price * weight
            totals["delta"] += g.delta * weight
            totals["gamma"] += g.gamma * weight
            totals["vega"] += g.vega * weight
            totals["theta"] += g.theta * weight
            totals["rho"] += g.rho * weight
            totals["vanna"] += g.vanna * weight
            totals["vomma"] += g.vomma * weight

        return Greeks(
            price=totals["price"],
            delta=totals["delta"],
            gamma=totals["gamma"],
            vega=totals["vega"],
            vega_per_point=totals["vega"] * 0.01,
            theta=totals["theta"],
            theta_per_day=totals["theta"] / 365.0,
            rho=totals["rho"],
            vanna=totals["vanna"],
            vomma=totals["vomma"],
        )

    def hedge_band(self, spot: float, gamma: float) -> float:
        """Fixed, or Whalley-Wilmott's cube-root width."""
        if self.config["hedge_band_mode"] == "fixed":
            return float(self.config["hedge_band"])
        cost = float(self.config["transaction_cost"])
        risk_aversion = max(float(self.config["risk_aversion"]), 1e-9)
        width = (1.5 * cost * spot * gamma**2 / risk_aversion) ** (1.0 / 3.0)
        return float(width)

    # ------------------------------------------------------------------ step
    def decide(self, view: MarketView) -> list[OrderIntent]:
        spot = view.mark_price
        step = view.step
        self._ensure_strip(spot, step)

        premium = self._client_flow(spot, step)
        self.option_premium += premium

        book = self.portfolio_greeks(spot, step)
        # The equity inventory from previous hedges offsets the option delta.
        net_delta = book.delta + view.inventory
        band = self.hedge_band(spot, abs(book.gamma))

        gamma_pnl = vega_pnl = theta_pnl = 0.0
        if self._last_spot is not None and self._last_greeks is not None:
            move = spot - self._last_spot
            # Gamma P&L over the step: the convexity the hedge cannot capture.
            gamma_pnl = 0.5 * self._last_greeks.gamma * move**2
            # Theta: time decay, in the same per-step units as everything else.
            theta_pnl = self._last_greeks.theta / float(self.config["steps_per_year"])
            # Vega: zero while implied volatility is a constant, but carried so the attribution stays.
            vega_pnl = 0.0

        intents: list[OrderIntent] = []
        hedge_slippage = 0.0
        if abs(net_delta) > band:
            # Hedge back to the edge of the band, not to zero: crossing the spread to reach an interior.
            target = math.copysign(band, net_delta)
            quantity = abs(net_delta - target)
            side = Side.SELL if net_delta > 0 else Side.BUY
            quantity = self._clamp_to_inventory_limit(side, quantity, view.inventory)
            if quantity >= 1.0:
                intents.append(
                    OrderIntent(
                        side=side,
                        quantity=quantity,
                        order_type=OrderType.MARKET,
                        reason=(
                            f"delta hedge: net {net_delta:+.1f} outside band "
                            f"{band:.1f} (gamma {book.gamma:+.2f})"
                        ),
                    )
                )
                # Crossing the spread costs half of it per share, which is the price of staying flat.
                if view.has_two_sided_book:
                    hedge_slippage = -quantity * (view.best_ask - view.best_bid) / 2.0
                self._hedge_trades_this_step = 1

        self.snapshots.append(
            GreeksSnapshot(
                step=step,
                spot=spot,
                portfolio_delta=book.delta,
                portfolio_gamma=book.gamma,
                portfolio_vega=book.vega,
                portfolio_theta=book.theta,
                gamma_pnl=gamma_pnl,
                vega_pnl=vega_pnl,
                theta_pnl=theta_pnl,
                hedge_slippage=hedge_slippage,
                option_premium=premium,
                hedge_trades=self._hedge_trades_this_step,
                hedge_band=band,
                net_delta_after_hedge=net_delta - sum(
                    (i.quantity if i.side is Side.BUY else -i.quantity) * -1 for i in intents
                ),
            )
        )
        self._hedge_trades_this_step = 0
        self._last_spot = spot
        self._last_greeks = book
        return intents

    # ------------------------------------------------------------- reporting
    def attribution(self) -> dict[str, Any]:
        """Totals for the run, plus the series behind them."""
        return {
            "gamma_pnl": sum(s.gamma_pnl for s in self.snapshots),
            "vega_pnl": sum(s.vega_pnl for s in self.snapshots),
            "theta_pnl": sum(s.theta_pnl for s in self.snapshots),
            "hedge_slippage": sum(s.hedge_slippage for s in self.snapshots),
            "option_premium": self.option_premium,
            "hedge_trades": sum(s.hedge_trades for s in self.snapshots),
            "delta_variance": float(
                np.var([s.portfolio_delta + 0.0 for s in self.snapshots])
                if self.snapshots
                else 0.0
            ),
            "final_positions": dict(self.positions),
            "snapshots": [s.as_dict() for s in self.snapshots],
        }
