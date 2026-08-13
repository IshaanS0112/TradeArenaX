"""Application configuration.

Every constant that changes a simulation result lives here: tick size, fee
schedule, the capital base that turns a PnL series into a return series, the
step-to-year scaling that makes a Sharpe ratio comparable to a published one,
and the default parameters of each agent archetype.

The reason this file exists rather than literals scattered through the services:
a simulation result is only meaningful if you can say what produced it. Every
run stores its resolved parameter set in ``simulations.price_process_config``
and each agent stores its own in ``agents.config``, so a number in the database
can be reproduced from the database.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import SelfTradePrevention


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Infrastructure -----------------------------------------------------
    database_url: str = (
        "postgresql+psycopg2://trade:trade@localhost:5432/tradearenax"
    )
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Market microstructure ---------------------------------------------
    # All prices are held internally as integer multiples of this tick. Two
    # orders can only share a price level if they are the *same* price, and
    # float arithmetic does not reliably produce equal floats from equal
    # intentions (100.10 - 0.05 + 0.05 != 100.10). Keying price levels on a
    # float dict is therefore a latent bug that shows up as phantom levels one
    # ulp apart. Integer ticks remove the class of bug entirely.
    tick_size: float = 0.01
    min_order_quantity: float = 1.0

    # Guard against a single request scheduling an unbounded amount of work.
    max_simulation_steps: int = 20_000
    max_agents_per_simulation: int = 12

    # --- Fees ---------------------------------------------------------------
    # Basis points of notional. Defaults are zero so that the textbook identity
    # (sum of all agents' PnL == 0) holds exactly in tests. Set a taker fee to
    # see the market maker's economics the way they actually work: the passive
    # side earns the spread, the aggressive side pays for immediacy.
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 0.0

    # --- Risk / accounting --------------------------------------------------
    # A PnL series alone cannot produce a Sharpe ratio: a return needs a
    # denominator. This is the notional equity each agent is assumed to deploy.
    capital_base: float = 100_000.0
    risk_free_rate_annual: float = 0.0
    # One simulation step is nominally one minute of a US equity session:
    # 252 trading days x 390 minutes. Used both for the GBM time increment and
    # to annualise Sharpe. Change it together, or neither.
    steps_per_year: int = 98_280

    self_trade_prevention: SelfTradePrevention = SelfTradePrevention.CANCEL_RESTING

    # --- Price process defaults --------------------------------------------
    default_initial_price: float = 100.0
    default_drift: float = 0.0  # mu, annualised
    default_volatility: float = 0.30  # sigma, annualised
    default_random_seed: int = 42

    # --- Agent defaults -----------------------------------------------------
    mm_spread: float = 0.10  # total quoted width, in price units
    mm_quote_size: float = 10.0
    mm_inventory_skew_k: float = 0.004  # price units of skew per unit of inventory
    mm_max_inventory: float = 200.0

    momentum_lookback: int = 20
    # Roughly one standard deviation of a 20-step return at the default 30%
    # annualised vol (0.30 * sqrt(20/98280) ~= 0.0043). A threshold materially
    # below one sigma fires on ordinary diffusion, which turns a trend follower
    # into a noise trader that pays the spread on every coin flip.
    momentum_threshold: float = 0.004
    momentum_base_size: float = 5.0
    momentum_max_size: float = 25.0
    momentum_max_inventory: float = 150.0

    reversion_window: int = 30
    reversion_z_threshold: float = 1.5
    reversion_base_size: float = 5.0
    reversion_max_size: float = 25.0
    reversion_max_inventory: float = 150.0

    # Fraction of the position dumped when |inventory| breaches the agent's
    # limit. 1.0 would be a full flatten, which is unrealistically violent and
    # guarantees the agent eats the whole book.
    liquidation_fraction: float = 0.5

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def dt(self) -> float:
        """Time increment of one simulation step, in years."""
        return 1.0 / self.steps_per_year

    @property
    def risk_free_per_step(self) -> float:
        return self.risk_free_rate_annual * self.dt

    @model_validator(mode="after")
    def _check_invariants(self) -> "Settings":
        if self.tick_size <= 0:
            raise ValueError("tick_size must be positive")
        if self.steps_per_year <= 0:
            raise ValueError("steps_per_year must be positive")
        if self.capital_base <= 0:
            raise ValueError(
                "capital_base must be positive: it is the denominator of every "
                "return, and therefore of Sharpe and drawdown."
            )
        if not 0.0 < self.liquidation_fraction <= 1.0:
            raise ValueError("liquidation_fraction must be in (0, 1]")
        if self.mm_spread <= 0:
            raise ValueError(
                "mm_spread must be positive: a non-positive quoted width means "
                "the maker crosses its own book."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
