"""The simulation loop: what happens, in what order, at every step."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.config import Settings, get_settings
from app.enums import AgentType, OrderStatus, OrderType, Side
from app.services.agents import Agent, MarketView, OrderIntent, build_agent
from app.services.order_book import Fill, Order, OrderBook
from app.services.performance_metrics import PerformanceSummary, summarise
from app.services.pnl import PositionTracker
from app.services.price_process import PriceProcess, VolatilityShock

logger = logging.getLogger("tradearenax.engine")


@dataclass
class AgentState:
    agent: Agent
    tracker: PositionTracker
    pnl_series: list[float] = field(default_factory=list)
    inventory_series: list[float] = field(default_factory=list)
    realized_series: list[float] = field(default_factory=list)
    unrealized_series: list[float] = field(default_factory=list)
    liquidation_events: list[int] = field(default_factory=list)
    # : Quote-reconciliation counters.
    quotes_kept: int = 0
    quotes_placed: int = 0

    @property
    def agent_id(self) -> str:
        return self.agent.agent_id


@dataclass
class StepRecord:
    step: int
    reference_price: float
    # : Mid of the touch, or ``None`` when a side of the book was empty.
    mid_price: float | None
    # : The price actually used to mark open inventory this step.
    mark_price: float
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    volatility: float
    trade_count: int
    shock_fired: bool


@dataclass
class SimulationResult:
    steps_run: int
    step_records: list[StepRecord]
    fills: list[Fill]
    orders: list[Order]
    # : The live book at the end of the run.
    book: OrderBook
    agent_states: dict[str, AgentState]
    summaries: dict[str, PerformanceSummary]
    reference_path: list[float]
    trade_prices: list[float]
    configured_volatility: float
    realized_volatility: float | None
    steps_with_no_two_sided_book: int
    warnings: list[str]


class SimulationEngine:
    """Owns one run: one book, one price path, N agents."""

    def __init__(
        self,
        price_process: PriceProcess,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.price_process = price_process
        self.book = OrderBook(
            tick_size=self.settings.tick_size,
            self_trade_prevention=self.settings.self_trade_prevention,
        )
        self.states: dict[str, AgentState] = {}
        self.trade_prices: list[float] = []
        # End-of-step mid prices.
        self.observed_prices: list[float] = []
        self.step_records: list[StepRecord] = []
        self.warnings: list[str] = []
        self._no_book_steps = 0
        self._step = 0

    def add_agent(self, agent_id: str, agent_type: AgentType | str, config: dict) -> AgentState:
        if agent_id in self.states:
            raise ValueError(f"duplicate agent id {agent_id}")
        if len(self.states) >= self.settings.max_agents_per_simulation:
            raise ValueError(
                f"at most {self.settings.max_agents_per_simulation} agents per simulation"
            )
        state = AgentState(
            agent=build_agent(agent_id, agent_type, config),
            tracker=PositionTracker(
                agent_id=agent_id,
                maker_fee_bps=self.settings.maker_fee_bps,
                taker_fee_bps=self.settings.taker_fee_bps,
            ),
        )
        self.states[agent_id] = state
        return state

    def run(self, steps: int) -> SimulationResult:
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

        for _ in range(steps):
            self._run_step()

        self.book.assert_invariants()
        return self._build_result(steps)

    def _run_step(self) -> None:
        self._step += 1
        step = self._step

        reference = self.price_process.advance()
        shock_fired = self.price_process.step_index in {
            s.step for s in self.price_process.shocks
        }

        # 2.
        views = {
            agent_id: self._build_view(step, reference, state)
            for agent_id, state in self.states.items()
        }

        # 3.
        order_ids = list(self.states)
        rotation = step % len(order_ids)
        submission_order = order_ids[rotation:] + order_ids[:rotation]

        step_fills: list[Fill] = []
        for agent_id in submission_order:
            state = self.states[agent_id]
            try:
                intents = state.agent.decide(views[agent_id])
            except Exception:  # pragma: no cover - defensive
                logger.exception("agent %s raised in decide(); treated as no action", agent_id)
                self.warnings.append(f"agent {agent_id} raised during step {step}")
                continue
            if state.agent.REQUOTES_EACH_STEP:
                intents = self._reconcile_quotes(state, intents, step)
            step_fills.extend(self._submit_intents(state, intents, step))

        # 4.
        self._apply_fills(step_fills)

        # 5.
        step_fills.extend(self._enforce_inventory_limits(step))

        # 6.
        mid = self.book.mid_price
        if mid is None:
            self._no_book_steps += 1
        mark = self._mark_price()
        self.observed_prices.append(mark)

        # 7.
        for state in self.states.values():
            unrealized = state.tracker.unrealized_pnl(mark)
            state.realized_series.append(state.tracker.realized_pnl)
            state.unrealized_series.append(unrealized)
            state.pnl_series.append(state.tracker.realized_pnl + unrealized)
            state.inventory_series.append(state.tracker.inventory)

        self.step_records.append(
            StepRecord(
                step=step,
                reference_price=reference,
                mid_price=mid,
                mark_price=mark,
                best_bid=self.book.best_bid,
                best_ask=self.book.best_ask,
                spread=self.book.spread,
                volatility=self.price_process.current_volatility(),
                trade_count=len(step_fills),
                shock_fired=shock_fired,
            )
        )

    def _mark_price(self) -> float:
        """Valuation price for open inventory."""
        mid = self.book.mid_price
        if mid is not None:
            return mid
        if self.trade_prices:
            return self.trade_prices[-1]
        if self.observed_prices:
            return self.observed_prices[-1]
        return self.price_process.price

    def _diff_quotes(
        self, state: AgentState, intents: list[OrderIntent]
    ) -> tuple[list[int], list[OrderIntent]]:
        """Diff a maker's desired quote set against what it already has resting."""
        resting = self.book.open_orders(state.agent_id)
        wanted: dict[tuple[Side, int], list[OrderIntent]] = {}
        passthrough: list[OrderIntent] = []

        for intent in intents:
            if intent.order_type is not OrderType.LIMIT or intent.price is None:
                passthrough.append(intent)  # market orders are never "resting"
                continue
            key = (intent.side, self.book.to_ticks(intent.price, intent.side))
            wanted.setdefault(key, []).append(intent)

        to_cancel: list[int] = []
        for order in resting:
            key = (order.side, order.price_ticks)
            candidates = wanted.get(key)
            matched = None
            if candidates:
                for candidate in candidates:
                    if abs(candidate.quantity - order.remaining) < 1e-9:
                        matched = candidate
                        break
            if matched is not None:
                candidates.remove(matched)
                if not candidates:
                    wanted.pop(key, None)
                state.quotes_kept += 1
            else:
                to_cancel.append(order.order_id)

        remaining_intents = passthrough + [i for group in wanted.values() for i in group]
        state.quotes_placed += len(remaining_intents)
        return to_cancel, remaining_intents

    def _reconcile_quotes(
        self, state: AgentState, intents: list[OrderIntent], step: int
    ) -> list[OrderIntent]:
        """Apply the quote diff immediately - the step engine's behaviour."""
        to_cancel, remaining_intents = self._diff_quotes(state, intents)
        for order_id in to_cancel:
            self.book.cancel(order_id, step=step)
        return remaining_intents

    def _build_view(self, step: int, reference: float, state: AgentState) -> MarketView:
        return MarketView(
            step=step,
            best_bid=self.book.best_bid,
            best_ask=self.book.best_ask,
            mid_price=self.book.mid_price,
            reference_price=reference,
            price_history=tuple(self.observed_prices),
            trade_prices=tuple(self.trade_prices),
            inventory=state.tracker.inventory,
            realized_pnl=state.tracker.realized_pnl,
            unrealized_pnl=state.tracker.unrealized_pnl(self._mark_price()),
            tick_size=self.settings.tick_size,
            bid_quantity=self._touch_quantity(Side.BUY),
            ask_quantity=self._touch_quantity(Side.SELL),
        )

    def _touch_quantity(self, side: Side) -> float:
        """Resting quantity at the best price on one side, zero if empty."""
        ticks = (
            self.book.best_bid_ticks() if side is Side.BUY else self.book.best_ask_ticks()
        )
        return 0.0 if ticks is None else self.book.depth_at(side, ticks)

    def _submit_intents(
        self, state: AgentState, intents: Iterable[OrderIntent], step: int
    ) -> list[Fill]:
        fills: list[Fill] = []
        for intent in intents:
            if intent.quantity < self.settings.min_order_quantity:
                continue
            _, new_fills = self.book.submit(
                agent_id=state.agent_id,
                side=intent.side,
                quantity=intent.quantity,
                price=intent.price,
                order_type=intent.order_type,
                step=step,
            )
            fills.extend(new_fills)
        return fills

    def _apply_fills(self, fills: list[Fill]) -> None:
        for fill in fills:
            price = self.book.to_price(fill.price_ticks)
            self.trade_prices.append(price)

            for agent_id, side in (
                (fill.buy_agent_id, Side.BUY),
                (fill.sell_agent_id, Side.SELL),
            ):
                state = self.states.get(agent_id)
                if state is None:  # pragma: no cover
                    continue
                is_maker = agent_id == fill.maker_agent_id
                state.tracker.apply_fill(
                    side=side,
                    quantity=fill.quantity,
                    price=price,
                    is_maker=is_maker,
                    step=fill.step,
                )

    def _enforce_inventory_limits(self, step: int) -> list[Fill]:
        """Force a partial liquidation for any agent past its inventory limit."""
        fills: list[Fill] = []
        for state in self.states.values():
            limit = state.agent.max_inventory
            score = state.tracker.inventory_risk_score(limit)
            if score <= 1.0:
                continue

            inventory = state.tracker.inventory
            qty = abs(inventory) * self.settings.liquidation_fraction
            if qty < self.settings.min_order_quantity:
                continue
            side = Side.SELL if inventory > 0 else Side.BUY

            state.agent.flags.append(
                f"step {step}: inventory risk score {score:.2f} > 1.0, "
                f"forced liquidation of {qty:.1f} via MARKET {side}"
            )
            state.liquidation_events.append(step)

            order, new_fills = self.book.submit(
                agent_id=state.agent_id,
                side=side,
                quantity=qty,
                order_type=OrderType.MARKET,
                step=step,
            )
            if order.status is OrderStatus.EXPIRED and order.filled_quantity == 0:
                self.warnings.append(
                    f"step {step}: agent {state.agent_id} could not liquidate - "
                    "no resting liquidity on the other side."
                )
            self._apply_fills(new_fills)
            fills.extend(new_fills)
        return fills

    def _build_result(self, steps: int) -> SimulationResult:
        summaries: dict[str, PerformanceSummary] = {}
        mark = self._mark_price()
        for agent_id, state in self.states.items():
            tracker = state.tracker
            summaries[agent_id] = summarise(
                pnl_series=state.pnl_series,
                inventory_series=state.inventory_series,
                realized_pnl=tracker.realized_pnl,
                unrealized_pnl=tracker.unrealized_pnl(mark),
                fees_paid=tracker.fees_paid,
                win_rate=tracker.win_rate,
                closed_round_trips=tracker.closed_round_trips,
                fill_count=tracker.fill_count,
                capital_base=self.settings.capital_base,
                risk_free_per_step=self.settings.risk_free_per_step,
                steps_per_year=self.settings.steps_per_year,
                max_inventory=state.agent.max_inventory,
            )

        if self._no_book_steps > steps * 0.25:
            self.warnings.append(
                f"{self._no_book_steps} of {steps} steps had a one-sided or empty "
                "book, so the mid price - and therefore unrealized PnL - fell back "
                "to the last trade or the reference price for those steps."
            )

        realized_vol = self.price_process.realized_volatility()
        if (
            self.price_process.shocks
            and realized_vol is not None
            and realized_vol > 2 * self.price_process.volatility
        ):
            self.warnings.append(
                f"Realized volatility ({realized_vol:.3f}) is far above the configured "
                f"sigma ({self.price_process.volatility:.3f}) because a jump "
                "discontinuity is not diffusion: a single large return inflates a "
                "sample standard deviation over a short path. This is the shock "
                "working as intended, not a mis-specified process."
            )

        total_fills = sum(s.tracker.fill_count for s in self.states.values())
        if total_fills == 0:
            self.warnings.append(
                "No trades occurred. Check that the directional agents' thresholds "
                "are reachable given the configured volatility, and that a market "
                "maker is quoting."
            )

        return SimulationResult(
            steps_run=steps,
            step_records=self.step_records,
            fills=list(self.book.trades),
            orders=self.book.all_orders,
            book=self.book,
            agent_states=self.states,
            summaries=summaries,
            reference_path=list(self.price_process.history),
            trade_prices=list(self.trade_prices),
            configured_volatility=self.price_process.volatility,
            realized_volatility=realized_vol,
            steps_with_no_two_sided_book=self._no_book_steps,
            warnings=self.warnings,
        )


def build_price_process(config: dict[str, Any], settings: Settings) -> PriceProcess:
    """Construct a price process from a stored ``price_process_config`` blob."""
    shocks = tuple(
        VolatilityShock(
            step=int(s["step"]),
            magnitude_pct=float(s["magnitude_pct"]),
            vol_multiplier=float(s.get("vol_multiplier", 1.0)),
            vol_half_life_steps=int(s.get("vol_half_life_steps", 50)),
        )
        for s in config.get("shocks", [])
    )
    return PriceProcess(
        initial_price=float(config.get("initial_price", settings.default_initial_price)),
        drift=float(config.get("drift", settings.default_drift)),
        volatility=float(config.get("volatility", settings.default_volatility)),
        dt=settings.dt,
        seed=int(config.get("random_seed", settings.default_random_seed)),
        shocks=shocks,
    )
