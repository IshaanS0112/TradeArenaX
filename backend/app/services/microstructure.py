"""Where the market maker's money comes from, and where it goes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from app.enums import Side

# Horizons, in steps, at which the realised spread is reported.
DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 20)


@dataclass(slots=True)
class TradeMeasures:
    step: int
    price: float
    quantity: float
    direction: int
    maker_agent_id: str
    taker_agent_id: str
    mid_at_trade: float | None
    # horizon -> (effective_half, realised_half, impact), all in price units.
    by_horizon: dict[int, tuple[float, float, float]]

    def at(self, horizon: int) -> tuple[float, float, float] | None:
        return self.by_horizon.get(horizon)

    @property
    def bps_denominator(self) -> float | None:
        if self.mid_at_trade in (None, 0.0):
            return None
        return self.mid_at_trade


def direction_of(aggressor_side: Side | str) -> int:
    """+1 for a buyer-initiated trade, -1 for a seller-initiated one."""
    return 1 if str(aggressor_side) == str(Side.BUY) else -1


def measure_trades(
    fills: Iterable[Any],
    mids: Sequence[float | None],
    tick_size: float,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> list[TradeMeasures]:
    """Decompose every fill."""
    out: list[TradeMeasures] = []
    for fill in fills:
        step = fill.step
        price = round(fill.price_ticks * tick_size, 10)
        direction = direction_of(fill.aggressor_side)
        mid_at_trade = _mid_at(mids, step)

        by_horizon: dict[int, tuple[float, float, float]] = {}
        if mid_at_trade is not None:
            for horizon in horizons:
                future_mid = _mid_at(mids, step + horizon)
                if future_mid is None:
                    continue
                effective = direction * (price - mid_at_trade)
                realised = direction * (price - future_mid)
                impact = direction * (future_mid - mid_at_trade)
                by_horizon[horizon] = (effective, realised, impact)

        out.append(
            TradeMeasures(
                step=step,
                price=price,
                quantity=fill.quantity,
                direction=direction,
                maker_agent_id=fill.maker_agent_id,
                taker_agent_id=fill.taker_agent_id,
                mid_at_trade=mid_at_trade,
                by_horizon=by_horizon,
            )
        )
    return out


def _mid_at(mids: Sequence[float | None], step: int) -> float | None:
    index = step - 1
    if index < 0 or index >= len(mids):
        return None
    return mids[index]


def _bps(value: float, mid: float) -> float:
    return 10_000.0 * value / mid


@dataclass(slots=True)
class AgentSpreadSummary:
    agent_id: str
    role: str  # "maker" | "taker"
    trade_count: int
    quantity: float
    effective_half_spread: float
    realised_half_spread: float
    price_impact: float
    effective_bps: float | None
    realised_bps: float | None
    impact_bps: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
            "trade_count": self.trade_count,
            "quantity": self.quantity,
            "effective_half_spread": self.effective_half_spread,
            "realised_half_spread": self.realised_half_spread,
            "price_impact": self.price_impact,
            "effective_bps": self.effective_bps,
            "realised_bps": self.realised_bps,
            "impact_bps": self.impact_bps,
        }


def summarise_by_agent(
    measures: Sequence[TradeMeasures], horizon: int
) -> list[AgentSpreadSummary]:
    """Quantity-weighted averages per agent, split by the role it played."""
    buckets: dict[tuple[str, str], dict[str, float]] = {}

    for measure in measures:
        values = measure.at(horizon)
        if values is None or measure.bps_denominator is None:
            continue
        effective, realised, impact = values
        quantity = measure.quantity
        mid = measure.bps_denominator

        for agent_id, role, sign in (
            (measure.maker_agent_id, "maker", 1.0),
            (measure.taker_agent_id, "taker", -1.0),
        ):
            bucket = buckets.setdefault(
                (agent_id, role),
                {
                    "trades": 0.0,
                    "quantity": 0.0,
                    "effective": 0.0,
                    "realised": 0.0,
                    "impact": 0.0,
                    "effective_bps": 0.0,
                    "realised_bps": 0.0,
                    "impact_bps": 0.0,
                },
            )
            bucket["trades"] += 1
            bucket["quantity"] += quantity
            bucket["effective"] += sign * effective * quantity
            bucket["realised"] += sign * realised * quantity
            bucket["impact"] += sign * impact * quantity
            bucket["effective_bps"] += sign * _bps(effective, mid) * quantity
            bucket["realised_bps"] += sign * _bps(realised, mid) * quantity
            bucket["impact_bps"] += sign * _bps(impact, mid) * quantity

    summaries: list[AgentSpreadSummary] = []
    for (agent_id, role), bucket in buckets.items():
        quantity = bucket["quantity"] or 1.0
        summaries.append(
            AgentSpreadSummary(
                agent_id=agent_id,
                role=role,
                trade_count=int(bucket["trades"]),
                quantity=bucket["quantity"],
                effective_half_spread=bucket["effective"] / quantity,
                realised_half_spread=bucket["realised"] / quantity,
                price_impact=bucket["impact"] / quantity,
                effective_bps=bucket["effective_bps"] / quantity,
                realised_bps=bucket["realised_bps"] / quantity,
                impact_bps=bucket["impact_bps"] / quantity,
            )
        )
    summaries.sort(key=lambda s: (s.agent_id, s.role))
    return summaries


def aggregate(measures: Sequence[TradeMeasures], horizon: int) -> dict[str, Any]:
    """Market-wide averages, quantity weighted, from the passive side."""
    usable = [
        m for m in measures if m.at(horizon) is not None and m.bps_denominator is not None
    ]
    if not usable:
        return {
            "horizon_steps": horizon,
            "trade_count": 0,
            "effective_half_spread": None,
            "realised_half_spread": None,
            "price_impact": None,
            "effective_bps": None,
            "realised_bps": None,
            "impact_bps": None,
        }

    total_qty = sum(m.quantity for m in usable)
    effective = sum(m.at(horizon)[0] * m.quantity for m in usable) / total_qty
    realised = sum(m.at(horizon)[1] * m.quantity for m in usable) / total_qty
    impact = sum(m.at(horizon)[2] * m.quantity for m in usable) / total_qty
    effective_bps = (
        sum(_bps(m.at(horizon)[0], m.bps_denominator) * m.quantity for m in usable) / total_qty
    )
    realised_bps = (
        sum(_bps(m.at(horizon)[1], m.bps_denominator) * m.quantity for m in usable) / total_qty
    )
    impact_bps = (
        sum(_bps(m.at(horizon)[2], m.bps_denominator) * m.quantity for m in usable) / total_qty
    )

    return {
        "horizon_steps": horizon,
        "trade_count": len(usable),
        "quantity": total_qty,
        "effective_half_spread": effective,
        "realised_half_spread": realised,
        "price_impact": impact,
        "effective_bps": effective_bps,
        "realised_bps": realised_bps,
        "impact_bps": impact_bps,
    }
