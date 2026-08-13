# TradeArena X — Architecture

## System shape

```
React + TS + Tailwind (nginx)          FastAPI                    PostgreSQL
        │                                 │                            │
        │  /api/* same-origin proxy       │  SQLAlchemy 2.0            │
        └────────────────────────────────►│───────────────────────────►│
                                          │
                            ┌─────────────┴──────────────┐
                            │      SimulationEngine       │
                            │  (owns one run end to end)  │
                            └─────────────┬──────────────┘
                     ┌────────────┬───────┴───────┬─────────────────┐
                     ▼            ▼               ▼                 ▼
                OrderBook     Agents (3)    PositionTracker    PriceProcess
                (matching)    (strategy)    (FIFO PnL)         (GBM + shocks)
                                                   │
                                                   ▼
                                          performance_metrics
                                     (Sharpe, Sortino, drawdown)
```

### Layering rule

`SimulationEngine` has **no database dependency**. It does not know what a session
is. `run_service.py` is the only module that knows both the engine and the ORM.
`OrderBook`, the agents, `PositionTracker` and `performance_metrics` know nothing
about either.

This is what makes the interesting parts testable in isolation: every agent test
constructs a `MarketView` by hand and asserts on the returned intents, with no
simulation running, no book, and no database. Every order book test drives the
matcher directly.

---

## What's real vs simulated

| Layer | Real / simulated | Notes |
|---|---|---|
| Matching engine | **Real** | Price-time priority, partial fills, cancels, self-trade prevention, integer tick grid. |
| Agent strategies | **Real** | Signals computed only from observable market data. |
| PnL accounting | **Real** | FIFO lots, position flips, mark-to-market, optional maker/taker fees. |
| Risk metrics | **Real** | Sharpe, Sortino, drawdown, win rate, inventory risk score. |
| Order flow | **Real** | Emerges from agent interaction; not scripted. |
| Reference price path | **Simulated** | GBM, exact log discretisation, seeded, with jump shocks. |
| Fair value known to the maker | **Simulated advantage** | The maker reads the latent path. Stated below. |
| Latency | **Absent** | All agents act on the same tick. |
| Market data feed / broker | **Absent** | There is none. The system cannot place an order. |

---

## Step ordering, and why it is the most consequential decision here

```
1. Advance the reference price process.               (the world moves)
2. Build one MarketView per agent, from identical     (no intra-step advantage)
   pre-action book state; each agent decides.
3. Submit intents in a rotated agent order.           (no fixed queue advantage)
4. Apply fills to position trackers.
5. Enforce inventory limits post-fill.
6. Append the end-of-step mark to the observable      (strictly historical)
   price history.
7. Record per-agent accounting.
```

**Step 2 builds all views before any agent acts.** If views were rebuilt per agent
inside the loop, the agent that happened to be second would react to the first
agent's orders within the same step — a one-step information advantage no venue
grants, which surfaces as suspiciously good PnL for whichever agent was created
first. `tests/test_no_lookahead.py::test_all_agents_in_a_step_see_the_identical_book`
asserts this.

**Step 3 rotates by step index.** Someone has to be first into the matcher, and
being first at a price level is worth money. A fixed order would systematically pay
one agent for its position in a Python list.

**Step 6 appends after the step completes.** A view at step *t* therefore carries
exactly *t-1* observations, and a signal cannot encode its own outcome. There is a
test that spies on every view built during a 50-step run and asserts
`len(price_history) == step - 1`.

### Quote reconciliation, not cancel-and-repost

A maker's resting quotes are **not** pulled at the top of the step. Its desired
quote set is diffed against what is already resting: orders at a price it still
wants are left alone, orders it no longer wants are cancelled, only new prices are
submitted. Two consequences, both the realistic ones:

- An unchanged price level **keeps its queue position**. Cancel-and-repost throws
  away time priority every step, which is a real and expensive mistake.
- Between steps the book holds the *previous* step's quotes, so a taker acting at
  step *t* can hit a quote priced off step *t-1*'s fair value. **That is adverse
  selection, and it is the maker's central risk.** A design that refreshes quotes
  before anyone can hit them deletes the very risk the inventory-skew logic exists
  to manage, and flatters the maker's PnL.

The engine tracks `quotes_kept` per maker so the effect is measurable rather than
asserted.

---

## Data model

```sql
simulations         id, name, duration_steps, price_process_config JSONB,
                    volatility_shock_config JSONB, engine_config JSONB,
                    run_summary JSONB, status, created_at

simulation_steps    simulation_id, step, reference_price, mid_price, mark_price,
                    best_bid, best_ask, spread, volatility, trade_count, shock_fired

agents              id, simulation_id, name, agent_type, config JSONB,
                    final_metrics JSONB, risk_flags JSONB, created_at

orders              id, simulation_id, agent_id, engine_order_id, sequence, step,
                    side, order_type, price, quantity, filled_quantity, status,
                    cancelled_at_step, cancelled_at_sequence

trades              id, simulation_id, step, buy_order_id, sell_order_id,
                    buy_agent_id, sell_agent_id, aggressor_side, price, quantity

agent_performance   agent_id, simulation_id, step, inventory, realized_pnl,
                    unrealized_pnl, total_pnl, mark_price, inventory_risk_score
```

### Columns added beyond the original schema sketch, and why

- **`simulations.engine_config`** — tick size, fee schedule, capital base, steps per
  year. A stored Sharpe of 6.54 cannot be verified without `capital_base` and
  `steps_per_year`, and neither is recoverable from the PnL series. Without this
  column the metric is a claim, not a result.
- **`simulation_steps`** (whole table) — the series behind every chart. The trade log
  alone cannot show a step where the book moved but nothing traded, which is most
  steps.
- **`orders.sequence`** — the engine's monotonic priority counter. It, not
  `created_at`, determined this order's place in the queue. Rebuilding a book from
  timestamps gives a different book than the one that matched.
- **`orders.cancelled_at_step` + `cancelled_at_sequence`** — see *Reconstruction*
  below. The sequence is load-bearing; the step alone is not enough.
- **`trades.aggressor_side`** — not derivable after the fact, and it is the field
  that separates who earned the spread from who paid for immediacy. A trade log
  without it cannot explain a market maker's PnL.
- **`agent_performance.mark_price`** — the price actually used for the unrealized
  leg. On a one-sided book the mid is null but the engine still had to mark against
  *something* (the last trade). Storing the null mid would leave the row's
  unrealized PnL unexplainable.

### `agents.config` stores the resolved config, not the request body

Defaults are filled in and validated before storage. A config that relied on a
default cannot be reproduced once that default changes. Relatedly, an unknown key
is a hard 422 — a typo'd `threshhold` that silently leaves the default in place
produces a run whose parameters are not what the caller asked for.

### Floats, and why prices are integers in the engine

The schema stores prices as `FLOAT`, matching the original spec. Internally the
book keys price levels on `int` ticks. Keyed on floats, `100.10 * 3 / 3` and
`100.10` are different levels — two prices no trader can distinguish, which the
matcher would then refuse to cross. Integers remove the class of bug. For a system
handling real money, `NUMERIC` in the database would be the right call too; for a
simulation whose comparisons are all relative, `FLOAT` plus an integer tick grid in
the engine is the correct trade.

---

## Reconstruction: replaying the book from the event log

`services/book_replay.py` rebuilds the book at any historical step by replaying
submissions and cancels **interleaved by sequence number** through the same
matching engine. It is exact, not approximate, because the persisted stream is a
complete event log.

**The bug that made this necessary.** The first implementation applied all of a
step's cancels before any of that step's submissions. The resting book came out
byte-identical to the live one — and half the executions vanished. Within a step, a
taker frequently hits a maker's stale quote *before* the maker's turn comes round
to pull it; front-loading the cancels deletes exactly those trades. The final book
matched because the missing trades were at prices that got consumed either way.

A reconstruction that agrees on shape while losing half the tape is the most
dangerous kind of wrong: nothing looks broken. It was caught by asserting
`replay.trades_replayed == len(result.fills)` against the live book, which is why
`SimulationResult` carries the live `OrderBook` — so the replay is checked against
ground truth rather than against another copy of its own logic.

The fix was to give cancels a position in the same sequence space as submissions.

---

## Risk enforcement is two layers, and only one of them normally runs

1. **Pre-trade clamping.** An agent shrinks any order that would breach its own
   inventory limit. A limit checked only after the fact is not a limit. This is the
   layer that actually operates — `test_pre_trade_clamping_keeps_inventory_inside_every_limit`
   asserts `|inventory| <= max_inventory` at every step of a 600-step trending run.

2. **Post-fill forced liquidation.** `|inventory| / max_inventory > 1.0` triggers a
   partial liquidation via MARKET order. A limit order would sit in the book while
   the position it is meant to reduce stays open — that is hope, not risk
   reduction.

Because layer 1 works, layer 2 is defence in depth and does not fire on a normal
run. That is stated rather than papered over: the trigger was *not* loosened to
`>= 1.0` to make it look active. Layer 2 is proven by constructing a breach
directly and asserting the net catches it, and by a separate test that a
liquidation into an empty book raises a warning instead of failing silently.

---

## Numerical decisions

**Exact GBM log form, not Euler.** `S·(1 + μdt + σ√dt·Z)` goes negative once
`σ√dt·Z < -1`; the exponential cannot. At 30% vol on a one-minute step that needs
a ~200σ draw, so Euler looks safe — right up until someone runs a 300%-vol stress
scenario on a daily step and the price goes through zero.

**Positive in theory is not representable in practice.** At σ=8 with dt=0.05 the
`-σ²/2` log-drift is -1.6 per step, so `log S` marches to -∞ and `exp()` underflows
to exactly 0.0 within a few thousand steps. Every downstream `log` and division
then silently yields `nan`. The process raises with the offending parameters in the
message instead.

**Guarded divisions everywhere a denominator can vanish.** Zero-dispersion Sharpe →
`None` (an agent that did not trade, not an infinitely good strategy). Zero moving
std → no z-score (a flat book makes this the common case, not the edge case).
Non-positive equity → 0.0 return rather than a division blow-up.

**Inventory snaps to zero through float dust.** Repeated fractional fills leaving a
1e-17 position is harmless in itself and poisonous in effect: `average_entry_price`
stays non-null, unrealized PnL keeps being computed against it, and the agent never
reads as flat.

---

## Metric definitions in force

| Metric | Definition |
|---|---|
| Return | `r_t = (E_t - E_{t-1}) / E_{t-1}`, `E = capital_base + total_pnl` |
| Sharpe (annualised) | `mean(r - rf) / sd(r - rf, ddof=1) · sqrt(steps_per_year)` |
| Sharpe (per step) | same without the annualisation factor; the comparable figure over a short run |
| Sortino | Sharpe with only negative returns in the denominator (RMS) |
| Max drawdown | `max(running_peak(E) - E)`, as % of the peak at that point |
| Win rate | profitable closed round trips / closed round trips |
| Realized PnL | FIFO lot matching; flips split across close and open legs |
| Unrealized PnL | `(mark - weighted_avg_open_entry) · inventory` |
| Inventory risk | `|inventory| / max_inventory` |

Sharpe is annualised because that is the standard, comparable form — but
annualising a sub-trading-day sample by `sqrt(98280) ≈ 313` is extrapolation by a
factor of hundreds, and it yields figures (40, 60) that no real strategy sustains.
The summary emits an explicit note when the sample is shorter than one trading day
and the dashboard tells the reader to compare by rank.

All of these are served live at `GET /meta/engine-config`, so the claim that the
metrics are computed rather than asserted is checkable without reading the source.

---

## Test strategy

210 tests, organised by the property being defended rather than by file:

| File | Defends |
|---|---|
| `test_order_book.py` | Price priority beats time priority; FIFO within a level; partial fill keeps queue position; execution at the resting price; market orders are IOC; lazy cancellation; three STP policies including termination when a level is entirely the aggressor's own; integer tick grid; book invariants |
| `test_pnl.py` | Open inventory realizes nothing; FIFO ordering; position flips; sign conventions on shorts; fees by role; float-dust flattening; two sides of one trade sum to zero |
| `test_performance_metrics.py` | Sharpe against a hand computation; `None` on zero dispersion; drawdown non-negative even when PnL is always negative; Sortino > Sharpe on right-skewed returns; short-sample annualisation flagged |
| `test_price_process.py` | Seed reproducibility; positivity under high vol; degenerate parameters raise; realized σ recovers configured σ over 20,000 steps; jump multiplies rather than replaces; vol decay half-life |
| `test_agents.py` | Symmetric quoting when flat; skew shifts both quotes without widening; one-sided cutoff; momentum and mean reversion take opposite sides on one ramp; z-score guards; exit rule; cooldowns; every incoherent config rejected |
| `test_no_lookahead.py` | Directional agents ignore the latent price (perturbed 50%, decisions unchanged); the maker *is* sensitive to it, so the previous test cannot pass vacuously; views carry exactly `step - 1` observations; all agents see identical state within a step |
| `test_simulation_engine.py` | **PnL conservation with and without fees**; inventory conservation; uncrossed book at end; run reproducibility; the maker earns the spread in a calm market; shock effects; both risk layers |
| `test_book_replay.py` | Replay reproduces the live book *and* the live trade count; intermediate steps are prefixes; cancels do not pile up; re-running replaces rather than appends |
| `test_api.py` | Full CRUD; 422 on every invalid payload; misspelled config keys rejected; shock past end-of-run rejected; downsampling preserves the final step and never drops a shock step; conservation residual exposed; book replay through HTTP |

The API tests run on SQLite in a temp file so a clone with no database container
executes the whole suite. The two dialect differences (JSONB, UUID) are handled by
column variants in `db/session.py`, so the code under test is the code that runs in
production.

---

## Lessons learned

**The first version produced zero trades in 300 steps.** Both directional agents
computed their signals from the executed trade tape. The tape is empty until
someone trades and nobody trades until a signal fires — a deadlock that no unit
test would have caught, because each agent was individually correct. Fixing it
meant asking what a real participant can actually observe: the quoted price series,
which exists from the first step a maker quotes. The lesson was that "what data
does this agent have a right to see" is a design question with a correct answer,
not an implementation detail.

**A crossed-book assertion that is too strict is a bug in the assertion.** Under the
SKIP self-trade policy an agent legitimately rests on top of its own order, so the
book *is* crossed — and nobody can execute against it. The invariant now fails only
when the crossing orders belong to different agents. Getting there required
separating "the book looks wrong" from "executable liquidity was left resting",
which are not the same statement.

**Replicating state is easy; replicating history is not.** The book replay matched
the live book perfectly while silently losing half the executions. The final state
agreed because the missing trades were at prices that got consumed either way. Only
comparing the *trade count* against the live book exposed it. Since then, anything
claiming to reproduce a system is checked against the system, never against a
second implementation of the same idea.

**Dead risk code is worse than no risk code.** Forced liquidation never fired,
because pre-trade clamping already bounded inventory. The tempting fix was to
loosen the trigger from `> 1.0` to `>= 1.0` so it would appear to work. The honest
fix was to test each layer for what it actually does and say in the docs that layer
two is a net. A risk control that has never executed is not a risk control, and
pretending otherwise is exactly the failure mode risk systems exist to prevent.

**Metrics need denominators, and denominators need units.** Sharpe on a raw PnL
series and drawdown as a fraction of peak PnL both produce numbers that look fine
and mean nothing — the second one goes *negative* on a losing run. Both were in the
original spec as one-liners. Writing down what each quantity is measured *in* was
what surfaced them.

**`create_all` is a decision with an expiry date.** It is right while the schema is
append-only and wrong the first time a column changes shape. Recorded here so the
next person does not have to infer whether it was considered.
