"""Discrete-event simulation: the same market, driven by a clock."""

from __future__ import annotations

import heapq
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config import Settings
from app.enums import AgentType, OrderType, Side
from app.services.agents import MarketView, OrderIntent
from app.services.latency import STEP_DURATION_US, LatencyProfile
from app.services.order_book import Fill
from app.services.price_process import PriceProcess
from app.services.simulation_engine import AgentState, SimulationEngine, StepRecord

logger = logging.getLogger("tradearenax.events")

PRICE_TICK = "PRICE_TICK"
AGENT_WAKE = "AGENT_WAKE"
ORDER_ARRIVAL = "ORDER_ARRIVAL"
CANCEL_ARRIVAL = "CANCEL_ARRIVAL"
FILL_NOTIFY = "FILL_NOTIFY"
STEP_CLOSE = "STEP_CLOSE"

# Phase ordinals inside a step, used as the second element of the sequence tuple.
_PHASE_TICK = 0
_PHASE_WAKE = 1
_PHASE_CLOSE = 9


@dataclass(order=True)
class Event:
    timestamp_us: int
    sequence: tuple[int, ...]
    kind: str = field(compare=False)
    agent_id: str | None = field(compare=False, default=None)
    payload: dict[str, Any] = field(compare=False, default_factory=dict)


@dataclass(slots=True)
class Publication:
    """One market-data snapshot, as the feed published."""

    step: int
    timestamp_us: int
    reference_price: float
    best_bid: float | None
    best_ask: float | None
    mid_price: float | None
    mark_price: float
    bid_quantity: float
    ask_quantity: float
    price_history: tuple[float, ...]
    trade_prices: tuple[float, ...]
    # agent_id -> (inventory, realized_pnl, unrealized_pnl)
    agent_state: dict[str, tuple[float, float, float]]


@dataclass(slots=True)
class EventRecord:
    """A flattened event, kept for the tape and for latency diagnostics."""

    sequence: tuple[int, ...]
    timestamp_us: int
    kind: str
    step: int
    agent_id: str | None
    payload: dict[str, Any]


@dataclass(slots=True)
class LatencyRace:
    """A fill that landed on a quote its owner had already tried to pull."""

    step: int
    fill_us: int
    maker_agent_id: str
    taker_agent_id: str
    price: float
    quantity: float
    maker_decided_us: int
    cancel_issued_us: int
    cancel_arrival_us: int
    margin_us: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "fill_us": self.fill_us,
            "maker_agent_id": self.maker_agent_id,
            "taker_agent_id": self.taker_agent_id,
            "price": self.price,
            "quantity": self.quantity,
            "maker_decided_us": self.maker_decided_us,
            "cancel_issued_us": self.cancel_issued_us,
            "cancel_arrival_us": self.cancel_arrival_us,
            "margin_us": self.margin_us,
        }


class EventDrivenEngine(SimulationEngine):
    """The V1 engine's semantics, re-expressed as a timed event queue."""

    # Cap on stored races.
    MAX_RACES = 200
    # Cap on the retained event tape, for the same reason.
    MAX_EVENTS = 20_000

    def __init__(
        self,
        price_process: PriceProcess,
        settings: Settings | None = None,
        latency_seed: int | None = None,
    ) -> None:
        super().__init__(price_process=price_process, settings=settings)
        self.latency: dict[str, LatencyProfile] = {}
        self.publications: list[Publication] = []
        self.events: list[EventRecord] = []
        self.races: list[LatencyRace] = []
        self._queue: list[Event] = []
        self._now_us = 0
        self._step_fill_count = 0
        self._shock_fired = False
        # Cancels in flight, by engine order id: (issued_us, arrival_us, decided_us).
        self._pending_cancels: dict[int, tuple[int, int, int]] = {}
        self._rng = np.random.default_rng(
            latency_seed if latency_seed is not None else price_process.seed
        )
        self._events_dropped = 0

    # ------------------------------------------------------------------ setup
    def add_agent(
        self,
        agent_id: str,
        agent_type: AgentType | str,
        config: dict,
        latency: LatencyProfile | dict | None = None,
    ) -> AgentState:
        state = super().add_agent(agent_id, agent_type, config)
        profile = (
            latency
            if isinstance(latency, LatencyProfile)
            else LatencyProfile.from_config(latency)
        )
        self.latency[agent_id] = profile
        return state

    @property
    def all_latencies_zero(self) -> bool:
        return all(p.is_zero for p in self.latency.values())

    # -------------------------------------------------------------------- run
    def run(self, steps: int) -> Any:
        self._validate_run(steps)

        for step in range(1, steps + 1):
            self._schedule(
                Event(
                    timestamp_us=(step - 1) * STEP_DURATION_US,
                    sequence=(step, _PHASE_TICK),
                    kind=PRICE_TICK,
                    payload={"step": step},
                )
            )
            self._schedule(
                Event(
                    timestamp_us=(step - 1) * STEP_DURATION_US + STEP_DURATION_US - 1,
                    sequence=(step, _PHASE_CLOSE),
                    kind=STEP_CLOSE,
                    payload={"step": step},
                )
            )

        while self._queue:
            event = heapq.heappop(self._queue)
            self._now_us = event.timestamp_us
            self._dispatch(event)

        self.book.assert_invariants()
        return self._build_result(steps)

    def _validate_run(self, steps: int) -> None:
        if steps <= 0:
            raise ValueError("steps must be positive")
        if steps > self.settings.max_simulation_steps:
            raise ValueError(
                f"steps {steps} exceeds max_simulation_steps "
                f"{self.settings.max_simulation_steps}"
            )
        if not self.states:
            raise ValueError(
                "a simulation with no agents has nothing to simulate: add agents first"
            )
        if not any(s.agent.REQUOTES_EACH_STEP for s in self.states.values()):
            self.warnings.append(
                "No liquidity-providing agent in this run. The directional agents "
                "can only take liquidity, so the book will stay empty and almost "
                "no trades will occur. Add a MARKET_MAKER."
            )

    # ------------------------------------------------------------- dispatching
    def _schedule(self, event: Event) -> None:
        heapq.heappush(self._queue, event)

    def _record(self, event: Event, step: int, payload: dict[str, Any] | None = None) -> None:
        if len(self.events) >= self.MAX_EVENTS:
            self._events_dropped += 1
            return
        self.events.append(
            EventRecord(
                sequence=event.sequence,
                timestamp_us=event.timestamp_us,
                kind=event.kind,
                step=step,
                agent_id=event.agent_id,
                payload=payload if payload is not None else dict(event.payload),
            )
        )

    def _dispatch(self, event: Event) -> None:
        if event.kind == PRICE_TICK:
            self._on_price_tick(event)
        elif event.kind == AGENT_WAKE:
            self._on_agent_wake(event)
        elif event.kind == ORDER_ARRIVAL:
            self._on_order_arrival(event)
        elif event.kind == CANCEL_ARRIVAL:
            self._on_cancel_arrival(event)
        elif event.kind == FILL_NOTIFY:
            self._record(event, event.payload.get("step", 0))
        elif event.kind == STEP_CLOSE:
            self._on_step_close(event)
        else:  # pragma: no cover - defensive
            raise ValueError(f"unknown event kind {event.kind}")

    # ----------------------------------------------------------------- handlers
    def _on_price_tick(self, event: Event) -> None:
        step = event.payload["step"]
        self._step = step
        self._step_fill_count = 0

        reference = self.price_process.advance()
        self._shock_fired = self.price_process.step_index in {
            s.step for s in self.price_process.shocks
        }
        self._publish(step, event.timestamp_us, reference)
        self._record(event, step, {"reference_price": reference})

        # Rotate which agent wakes first.
        agent_ids = list(self.states)
        rotation = step % len(agent_ids)
        order = agent_ids[rotation:] + agent_ids[:rotation]

        for slot, agent_id in enumerate(order):
            profile = self.latency[agent_id]
            delay = profile.sample_in(self._rng)
            self._schedule(
                Event(
                    timestamp_us=event.timestamp_us + delay,
                    sequence=(step, _PHASE_WAKE, slot),
                    kind=AGENT_WAKE,
                    agent_id=agent_id,
                    payload={"step": step, "feed_delay_us": delay},
                )
            )

    def _on_agent_wake(self, event: Event) -> None:
        step = event.payload["step"]
        agent_id = event.agent_id
        assert agent_id is not None
        state = self.states[agent_id]
        profile = self.latency[agent_id]

        publication = self._publication_for(event.timestamp_us - profile.latency_in_us)
        if publication is None:  # pragma: no cover - a wake always follows a tick
            return
        view = self._view_from(publication, state)

        try:
            intents = state.agent.decide(view)
        except Exception:  # pragma: no cover - defensive, mirrors the step engine
            logger.exception("agent %s raised in decide(); treated as no action", agent_id)
            self.warnings.append(f"agent {agent_id} raised during step {step}")
            return

        to_cancel: list[int] = []
        if state.agent.REQUOTES_EACH_STEP:
            to_cancel, intents = self._diff_quotes(state, list(intents))

        self._record(
            event,
            step,
            {
                "step": step,
                "observed_step": publication.step,
                "feed_delay_us": event.payload.get("feed_delay_us", 0),
                "intents": len(intents),
                "cancels": len(to_cancel),
            },
        )

        child = 0
        # Cancels leave before the new quotes, exactly as the step engine did: a maker pulls what it no.
        for order_id in to_cancel:
            arrival = event.timestamp_us + profile.sample_out(self._rng)
            self._pending_cancels[order_id] = (
                event.timestamp_us,
                arrival,
                event.timestamp_us,
            )
            self._schedule(
                Event(
                    timestamp_us=arrival,
                    sequence=event.sequence + (child,),
                    kind=CANCEL_ARRIVAL,
                    agent_id=agent_id,
                    payload={"step": step, "order_id": order_id, "issued_us": event.timestamp_us},
                )
            )
            child += 1

        for intent in intents:
            if intent.quantity < self.settings.min_order_quantity:
                continue
            arrival = event.timestamp_us + profile.sample_out(self._rng)
            self._schedule(
                Event(
                    timestamp_us=arrival,
                    sequence=event.sequence + (child,),
                    kind=ORDER_ARRIVAL,
                    agent_id=agent_id,
                    payload={
                        "step": step,
                        "intent": intent,
                        "issued_us": event.timestamp_us,
                    },
                )
            )
            child += 1

    def _on_order_arrival(self, event: Event) -> None:
        step = event.payload["step"]
        intent: OrderIntent = event.payload["intent"]
        agent_id = event.agent_id
        assert agent_id is not None

        order, fills = self.book.submit(
            agent_id=agent_id,
            side=intent.side,
            quantity=intent.quantity,
            price=intent.price,
            order_type=intent.order_type,
            step=step,
        )
        self._record(
            event,
            step,
            {
                "step": step,
                "side": str(intent.side),
                "price": intent.price,
                "quantity": intent.quantity,
                "order_type": str(intent.order_type),
                "engine_order_id": order.order_id,
                "fills": len(fills),
                "flight_us": event.timestamp_us - event.payload["issued_us"],
            },
        )
        self._settle_fills(fills, step, event)

    def _on_cancel_arrival(self, event: Event) -> None:
        step = event.payload["step"]
        order_id = event.payload["order_id"]
        cancelled = self.book.cancel(order_id, step=step)
        self._pending_cancels.pop(order_id, None)
        self._record(
            event,
            step,
            {
                "step": step,
                "order_id": order_id,
                "cancelled": cancelled,
                "flight_us": event.timestamp_us - event.payload["issued_us"],
            },
        )

    def _on_step_close(self, event: Event) -> None:
        step = event.payload["step"]
        self._step = step

        # Forced liquidation runs at the close, after this step's fills have been applied - the same.
        liquidation_fills = self._enforce_inventory_limits(step)
        self._step_fill_count += len(liquidation_fills)

        mid = self.book.mid_price
        if mid is None:
            self._no_book_steps += 1
        mark = self._mark_price()
        self.observed_prices.append(mark)

        for state in self.states.values():
            unrealized = state.tracker.unrealized_pnl(mark)
            state.realized_series.append(state.tracker.realized_pnl)
            state.unrealized_series.append(unrealized)
            state.pnl_series.append(state.tracker.realized_pnl + unrealized)
            state.inventory_series.append(state.tracker.inventory)

        self.step_records.append(
            StepRecord(
                step=step,
                reference_price=self.price_process.history[-1],
                mid_price=mid,
                mark_price=mark,
                best_bid=self.book.best_bid,
                best_ask=self.book.best_ask,
                spread=self.book.spread,
                volatility=self.price_process.current_volatility(),
                trade_count=self._step_fill_count,
                shock_fired=self._shock_fired,
            )
        )
        self._record(event, step, {"step": step, "trade_count": self._step_fill_count})

    # ---------------------------------------------------------------- internals
    def _settle_fills(self, fills: list[Fill], step: int, cause: Event) -> None:
        if not fills:
            return
        self._apply_fills(fills)
        self._step_fill_count += len(fills)

        for index, fill in enumerate(fills):
            price = self.book.to_price(fill.price_ticks)
            self._note_race(fill, price, step)
            for agent_id in {fill.buy_agent_id, fill.sell_agent_id}:
                profile = self.latency.get(agent_id)
                if profile is None:  # pragma: no cover - defensive
                    continue
                self._schedule(
                    Event(
                        timestamp_us=cause.timestamp_us + profile.sample_in(self._rng),
                        sequence=cause.sequence + (1_000 + index,),
                        kind=FILL_NOTIFY,
                        agent_id=agent_id,
                        payload={
                            "step": step,
                            "price": price,
                            "quantity": fill.quantity,
                            "is_maker": agent_id == fill.maker_agent_id,
                        },
                    )
                )

    def _note_race(self, fill: Fill, price: float, step: int) -> None:
        """Record a fill that beat its own maker's cancel to the book."""
        pending = self._pending_cancels.get(fill.maker_order_id)
        if pending is None or len(self.races) >= self.MAX_RACES:
            return
        issued_us, arrival_us, decided_us = pending
        self.races.append(
            LatencyRace(
                step=step,
                fill_us=self._now_us,
                maker_agent_id=fill.maker_agent_id,
                taker_agent_id=fill.taker_agent_id,
                price=price,
                quantity=fill.quantity,
                maker_decided_us=decided_us,
                cancel_issued_us=issued_us,
                cancel_arrival_us=arrival_us,
                margin_us=arrival_us - self._now_us,
            )
        )

    def _publish(self, step: int, timestamp_us: int, reference: float) -> None:
        mark = self._mark_price()
        self.publications.append(
            Publication(
                step=step,
                timestamp_us=timestamp_us,
                reference_price=reference,
                best_bid=self.book.best_bid,
                best_ask=self.book.best_ask,
                mid_price=self.book.mid_price,
                mark_price=mark,
                bid_quantity=self._touch_quantity(Side.BUY),
                ask_quantity=self._touch_quantity(Side.SELL),
                price_history=tuple(self.observed_prices),
                trade_prices=tuple(self.trade_prices),
                agent_state={
                    agent_id: (
                        state.tracker.inventory,
                        state.tracker.realized_pnl,
                        state.tracker.unrealized_pnl(mark),
                    )
                    for agent_id, state in self.states.items()
                },
            )
        )

    def _publication_for(self, cutoff_us: int) -> Publication | None:
        """The newest publication an agent could have received by ``cutoff_us``."""
        chosen = None
        for publication in self.publications:
            if publication.timestamp_us <= cutoff_us:
                chosen = publication
            else:
                break
        return chosen or (self.publications[0] if self.publications else None)

    def _view_from(self, publication: Publication, state: AgentState) -> MarketView:
        inventory, realized, unrealized = publication.agent_state.get(
            state.agent_id, (0.0, 0.0, 0.0)
        )
        return MarketView(
            step=publication.step,
            best_bid=publication.best_bid,
            best_ask=publication.best_ask,
            mid_price=publication.mid_price,
            reference_price=publication.reference_price,
            price_history=publication.price_history,
            trade_prices=publication.trade_prices,
            inventory=inventory,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            tick_size=self.settings.tick_size,
            bid_quantity=publication.bid_quantity,
            ask_quantity=publication.ask_quantity,
        )

    # ------------------------------------------------------------------ results
    def _build_result(self, steps: int) -> Any:
        result = super()._build_result(steps)
        if self._events_dropped:
            self.warnings.append(
                f"event tape truncated at {self.MAX_EVENTS} events; "
                f"{self._events_dropped} further events were not retained."
            )
        return result

    def latency_summary(self) -> dict[str, Any]:
        """Per-agent adverse-fill counts and the configured profiles."""
        adverse: dict[str, int] = {agent_id: 0 for agent_id in self.states}
        for race in self.races:
            adverse[race.maker_agent_id] = adverse.get(race.maker_agent_id, 0) + 1
        return {
            "step_duration_us": STEP_DURATION_US,
            "profiles": {a: p.as_dict() for a, p in self.latency.items()},
            "adverse_fills": adverse,
            "races_recorded": len(self.races),
            "races_capped_at": self.MAX_RACES,
        }
