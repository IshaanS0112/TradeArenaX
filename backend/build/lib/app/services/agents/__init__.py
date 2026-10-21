"""Agent archetypes and the factory that builds them from stored config."""

from __future__ import annotations

from app.enums import AgentType
from app.services.agents.base import Agent, MarketView, OrderIntent
from app.services.agents.market_maker import MarketMakerAgent
from app.services.agents.mean_reversion import MeanReversionAgent
from app.services.agents.momentum import MomentumAgent
from app.services.agents.noise_trader import NoiseTraderAgent
from app.services.agents.options_maker import OptionsMarketMakerAgent

_REGISTRY: dict[AgentType, type[Agent]] = {
    AgentType.MARKET_MAKER: MarketMakerAgent,
    AgentType.MOMENTUM: MomentumAgent,
    AgentType.MEAN_REVERSION: MeanReversionAgent,
    AgentType.NOISE_TRADER: NoiseTraderAgent,
    AgentType.OPTIONS_MAKER: OptionsMarketMakerAgent,
}


def build_agent(agent_id: str, agent_type: AgentType | str, config: dict) -> Agent:
    key = AgentType(agent_type)
    try:
        cls = _REGISTRY[key]
    except KeyError:  # pragma: no cover - AgentType() already rejects unknowns
        raise ValueError(f"unknown agent type: {agent_type}") from None
    return cls(agent_id=agent_id, config=cls.resolve_config(config))


def default_config(agent_type: AgentType | str) -> dict:
    return _REGISTRY[AgentType(agent_type)].resolve_config({})


__all__ = [
    "Agent",
    "MarketView",
    "OrderIntent",
    "MarketMakerAgent",
    "MomentumAgent",
    "MeanReversionAgent",
    "NoiseTraderAgent",
    "OptionsMarketMakerAgent",
    "build_agent",
    "default_config",
]
