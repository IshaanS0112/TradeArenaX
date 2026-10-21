"""The library front door: drive the engine from a notebook, with no server.

The engine has always been importable - it has no database dependency and
``scripts/demo_run.py`` drives it directly - but using it meant knowing
``PriceProcess``, ``VolatilityShock``, ``Settings``, ``AgentType`` and
``EventDrivenEngine`` and wiring them together yourself. That is a reasonable
ask of someone extending the engine and an unreasonable one of an analyst who
wants a number.

    from app.sdk import Desk, Shock, simulate

    run = simulate(
        steps=1000,
        seed=42,
        shocks=[Shock(step=500, pct=-8.0)],
        agents=[
            Desk("mm", "MARKET_MAKER", latency_out_us=1200, latency_jitter_us=300),
            Desk("flow", "NOISE_TRADER", activity=0.5),
        ],
    )

    run.summary["total_trades"]     # 907
    run.conservation                # -5.7e-14
    run.spread()                    # effective / realised / impact per desk
    run.frame("agents")             # pandas, when it is installed

Every number here comes from the same functions the HTTP layer calls, so the
API and a notebook cannot disagree about the same run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings, get_settings
from app.enums import AgentType
from app.services import reporting
from app.services.event_engine import EventDrivenEngine
from app.services.price_process import PriceProcess, VolatilityShock
from app.services.simulation_engine import SimulationResult

_LATENCY_KEYS = frozenset({"latency_in_us", "latency_out_us", "latency_jitter_us"})


@dataclass(frozen=True, slots=True)
class Shock:
    """A scheduled jump, and the volatility regime it leaves behind."""

    step: int
    pct: float
    vol_multiplier: float = 3.0
    half_life_steps: int = 50


@dataclass
class Desk:
    """One participant. Strategy settings and latency are both keyword arguments.

    Latency keys are routed to the latency profile and everything else to the
    strategy, so a desk reads as one thing rather than as two dictionaries.
    """

    id: str
    type: AgentType | str
    config: dict[str, Any] = field(default_factory=dict)
    latency: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        id: str,
        type: AgentType | str,
        config: dict[str, Any] | None = None,
        latency: dict[str, Any] | None = None,
        **options: Any,
    ) -> None:
        self.id = id
        self.type = type
        self.config = {**(config or {}), **{k: v for k, v in options.items() if k not in _LATENCY_KEYS}}
        self.latency = {**(latency or {}), **{k: v for k, v in options.items() if k in _LATENCY_KEYS}}


class Run:
    """A finished simulation, and the measurements taken from it."""

    def __init__(
        self, result: SimulationResult, settings: Settings, latency: dict[str, Any]
    ) -> None:
        self.result = result
        self.settings = settings
        self.latency = latency
        names = {agent_id: agent_id for agent_id in result.agent_states}
        self.summary: dict[str, Any] = reporting.build_run_summary("local", result, names)

    @property
    def conservation(self) -> float:
        """Sum of every desk's PnL plus fees. Must be zero to float noise."""
        return self.summary["pnl_conservation_residual"]

    def agents(self) -> list[dict[str, Any]]:
        """One row per desk: PnL, Sharpe, drawdown, fills, final inventory."""
        return [
            {"agent_id": agent_id, **metrics}
            for agent_id, metrics in self.summary["agent_metrics"].items()
        ]

    def steps(self) -> list[dict[str, Any]]:
        """The market series: reference, mid, touch, spread, volatility, trades."""
        return [
            {
                "step": record.step,
                "reference_price": record.reference_price,
                "mid_price": record.mid_price,
                "mark_price": record.mark_price,
                "best_bid": record.best_bid,
                "best_ask": record.best_ask,
                "spread": record.spread,
                "volatility": record.volatility,
                "trade_count": record.trade_count,
                "shock_fired": record.shock_fired,
            }
            for record in self.result.step_records
        ]

    def spread(self, horizon: int | None = None) -> list[dict[str, Any]]:
        """Adverse selection: effective = realised + impact, from the passive side."""
        measured = reporting.microstructure_summary(self.result, self.settings)
        key = str(horizon if horizon is not None else measured["default_horizon_steps"])
        return measured["by_agent"].get(key, [])

    def races(self) -> list[dict[str, Any]]:
        """Fills that beat their own maker's cancel, with the margin in microseconds."""
        return list(self.latency.get("races", []))

    def greeks(self) -> dict[str, Any]:
        """Gamma, theta, vega and hedging slippage, per options desk."""
        return reporting.greeks_summary(self.result)

    def frame(self, what: str = "agents"):
        """The same rows as a pandas DataFrame. Requires pandas, which is optional."""
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise ImportError("frame() needs pandas: pip install pandas") from exc
        rows = {"agents": self.agents, "steps": self.steps, "spread": self.spread}
        if what not in rows:
            raise ValueError(f"unknown frame {what!r}; try one of {sorted(rows)}")
        return pd.DataFrame(rows[what]())


def simulate(
    steps: int = 1000,
    *,
    agents: list[Desk] | None = None,
    seed: int = 42,
    volatility: float = 0.30,
    drift: float = 0.0,
    initial_price: float = 100.0,
    shocks: list[Shock] | None = None,
    settings: Settings | None = None,
) -> Run:
    """Run a simulation and return its measurements. No database, no server.

    With no agents the default desk mix is used: a market maker, momentum and
    mean reversion - the three that make a market without further configuration.
    """
    settings = settings or get_settings()
    process = PriceProcess(
        initial_price=initial_price,
        drift=drift,
        volatility=volatility,
        dt=settings.dt,
        seed=seed,
        shocks=tuple(
            VolatilityShock(
                step=s.step,
                magnitude_pct=s.pct,
                vol_multiplier=s.vol_multiplier,
                vol_half_life_steps=s.half_life_steps,
            )
            for s in (shocks or [])
        ),
    )

    engine = EventDrivenEngine(price_process=process, settings=settings)
    for desk in agents or _DEFAULT_DESKS:
        engine.add_agent(desk.id, desk.type, desk.config, latency=desk.latency or None)

    result = engine.run(steps)
    latency = {
        **engine.latency_summary(),
        "races": [race.as_dict() for race in engine.races],
    }
    return Run(result, settings, latency)


_DEFAULT_DESKS = (
    Desk("mm", AgentType.MARKET_MAKER),
    Desk("mom", AgentType.MOMENTUM),
    Desk("rev", AgentType.MEAN_REVERSION),
)
