# TradeArena X

**Market-making and trading strategy simulation on a real limit order book.**

A price-time-priority matching engine, three competing agent archetypes
(inventory-aware market making, momentum, mean reversion), and a strategy
comparison layer reporting Sharpe ratio, max drawdown, win rate and inventory
risk — under normal conditions and under injected volatility shocks.

FastAPI · React + TypeScript + Tailwind · PostgreSQL · Docker · 210 tests

---

## What is real and what is simulated

This matters more than anything else in the README, so it goes first.

| Component | Status |
|---|---|
| Limit order book, price-time priority matching | **Real.** Heap of price levels, per-level FIFO queues, partial fills, cancels, self-trade prevention. |
| All three agent strategies | **Real.** Every signal computed from observable market data only. |
| PnL accounting | **Real.** FIFO lot matching, position flips handled, mark-to-market on open inventory. |
| Risk and performance metrics | **Real.** Sharpe, Sortino, max drawdown, win rate, inventory risk scoring. |
| **The price process** | **Synthetic.** Geometric Brownian motion with configurable jump discontinuities. |

**There is no live market data feed, no broker integration, and no capital at
risk. This system cannot place an order anywhere.** It is a strategy research and
education tool, which is the standard setting for this kind of work — quant
research runs on generated or historical paths, not on live capital.

Nothing here is investment advice.

---

## Why a synthetic price process is the right choice, not a shortcut

The value of this project is in the microstructure mechanics: how a matching
engine assigns priority, how a market maker's quotes lean against inventory, how
FIFO lot matching handles a position flip, and how those interact when the price
gaps 8% in one step. None of that gets more real by pulling a live feed — it gets
harder to test and impossible to reproduce.

What a synthetic path buys instead:

- **Reproducibility.** Same seed and config, same result, exactly. Every agent
  comparison is a controlled experiment rather than a different random draw.
- **Controlled stress.** You can put a -15% jump at step 400 with a 4x volatility
  regime behind it and watch each agent react. You cannot schedule a real crash.

The honest cost is stated plainly below under *Known limitations*.

---

## Quick start

```bash
docker compose up --build
```

- Dashboard: http://localhost:5173
- API docs: http://localhost:8000/docs
- Every formula in force: http://localhost:8000/meta/engine-config

Without Docker:

```bash
# Backend
cd backend
pip install -r requirements-dev.txt
export DATABASE_URL="sqlite:///./tradearenax.db"   # or a Postgres URL
uvicorn app.main:app --reload

# Frontend, in another shell
cd frontend && npm install && npm run dev
```

Run the engine with no database and no HTTP layer at all:

```bash
cd backend && python scripts/demo_run.py --steps 1000 --shock-step 500 --shock-pct -8
```

```
Steps: 1000   Trades: 334   Orders: 2272
Reference price: 100.00 -> 88.19
Volatility  configured 0.300  realized 0.946
Steps without a two-sided book: 35

agent    total pnl    realized    unreal    sharpe   maxDD%    win%  trips  fills     inv
mom         177.11      238.44    -61.33      3.50     0.31    46.1    115    130    -116
mm          115.38       26.58     88.81      2.26     0.63    67.0    315    334     130
rev        -292.49     -286.60     -5.89    -21.06     0.29     8.6    185    204     -14

PnL conservation check: sum(total_pnl) + fees = -0.0000000000
  warning: Realized volatility (0.946) is far above the configured sigma (0.300)
  because a jump discontinuity is not diffusion...
```

The realized-vs-configured volatility gap in that warning is not a bug and the
engine says so: a single -8% jump is not diffusion, and one large return inflates a
sample standard deviation over a 1,000-step path.

## Tests

```bash
cd backend && python -m pytest        # 210 tests
cd frontend && npm run typecheck && npm run build
```

---

## The mechanics, in the order they matter

### 1. Limit order book — price-time priority

Two heaps of *price levels*, not of orders:

```
bids: max-heap of int ticks, each level a FIFO deque
asks: min-heap of int ticks, each level a FIFO deque
```

Price priority comes from the heap; time priority from the deque. Keeping orders
in per-level FIFOs is what makes time priority survive a partial fill — a resting
order that is half filled stays at the head of its queue instead of being
re-inserted behind orders that arrived after it.

Three details a naive implementation gets wrong, and this one does not:

- **Prices are integers.** A level is `round(price / tick_size)`. Keyed on floats,
  `100.10 * 3 / 3` and `100.10` land on different levels — two prices no trader
  can tell apart, which a matching engine will then refuse to cross. There is a
  test for exactly this.
- **Time priority uses a sequence counter, not a clock.** Wall-clock timestamps
  tie constantly inside one simulation step, and a tie in the priority key means
  the matching order is whatever the container happened to do — not a
  specification.
- **Self-trade prevention.** A market maker quotes both sides; the moment its
  spread inverts it can trade with itself and book a fictional profit. Three
  policies are implemented (cancel-resting, cancel-incoming, skip) and the
  crossed-book invariant understands that a same-agent cross under *skip* is
  legitimate while any cross across agents is a bug.

Execution happens at the **resting** order's price. The passive side set the
terms; the aggressor accepted them. Filling at the incoming limit would hand the
aggressor a rebate it never earned.

### 2. Market maker — inventory-aware quoting

```
skew = k * inventory
bid  = fair_value - half_spread - skew
ask  = fair_value + half_spread - skew
```

The skew subtracts from **both** quotes. Long inventory pushes the whole quote
down — the bid gets less attractive to sellers, the ask more attractive to buyers
— so the flow it attracts reduces the position. It does not widen the spread. The
width is a response to *volatility*; the shift is a response to *inventory*, and
those two get conflated constantly.

This is the simplified Avellaneda–Stoikov form: the linear inventory term with a
fixed base width. The full result derives both the reservation-price shift and
the optimal half-spread from a utility function with a risk-aversion parameter and
a finite horizon. The simplification reproduces the qualitative behaviour without
the closed-form machinery, and is labelled as such rather than claimed as the
full model.

Two additions beyond the base formula, both of which change outcomes:

- **Volatility widening**, capped at a configurable multiple of the base spread. A
  fixed width during a shock is how a market maker gets run over — every quote is
  stale by the time it is hit.
- **A hard one-sided cutoff** at 85% of the inventory limit. Skew is a price
  incentive; a sufficiently one-directional market will pay the skew and keep
  filling you.

### 3. Momentum vs mean reversion — the same series, opposite signs

```
momentum:       (P_t - P_{t-N}) / P_{t-N}   >  threshold  ->  BUY
mean reversion: (P_t - MA_w) / SD_w         >  threshold  ->  SELL
```

Momentum trades *with* the deviation; mean reversion trades *against* it. Both
read the identical price series and disagree about whether a move is information
or noise. There is a test that feeds one ramp to both agents and asserts they take
opposite sides.

Both compute signals from the **observable mid-price series** — what any
participant could read off the public book — not from the latent GBM path, and not
from the trade tape. (Keying off the tape deadlocks: the tape is empty until
someone trades, and nobody trades until a signal fires. That was the first version
of this engine, and it produced zero trades in 300 steps.)

### 4. PnL — FIFO lot matching

Realized PnL uses FIFO lot matching, not `sum(sells) - sum(buys)`. That shortcut
is not realized PnL at all — it is cash flow, and it reports a fictional 1,000
loss on a flat trade the moment an agent is net long.

The case that makes it non-trivial is a **position flip**. Long 10 at 100, then
sell 25 at 105: that closes 10 long for +50 and *opens* 15 short at 105. Handled
as one signed number, the opening 15 gets booked as if it had closed something.

### 5. Metrics — where the textbook one-liners are wrong

**Sharpe needs a return series, and PnL is not one.** `(mean - rf) / sd` is
dimensionless only if the numerator is a return; PnL is currency, and its ratio
scales with position size, comparable to no published figure. Here the PnL series
is converted to returns against an equity curve (`capital_base + PnL`) and
annualised by `sqrt(steps_per_year)`. Both the annualised and the per-step figure
are reported, and a run shorter than one trading day is explicitly flagged as
over-extrapolated rather than left to impress.

**Max drawdown as a percentage of peak PnL breaks when PnL is negative.** Peak
-50, trough -200 gives `(peak - trough) / peak` = -300%: a negative drawdown,
which is not a quantity. Drawdown is computed on the equity curve, whose peak is
bounded below by the capital base.

Zero-dispersion Sharpe returns `None`, not infinity. An agent with no closed
round trips has `None` win rate, not 0% — printing 0% invites the reader to
conclude it lost every trade.

### 6. Volatility shocks

`dS = μS·dt + σS·dW`, integrated in its **exact log form**:

```
S_{t+1} = S_t · exp((μ - σ²/2)·dt + σ·√dt·Z)
```

not an Euler step, which goes negative as soon as `σ√dt·Z < -1`. A shock is a
jump discontinuity multiplied onto that step's diffusion, and it optionally leaves
elevated volatility behind it that decays with a configurable half-life — that is
volatility clustering, which is what makes a shock a stress test rather than one
bad tick the agents forget immediately.

---

## The check that makes the rest of the numbers trustworthy

Every trade moves value between two participants. Nothing enters or leaves except
fees. So:

```
sum(all agents' total PnL) + fees collected == 0
```

This runs on **every simulation**, not just in tests, and the residual is
returned by the comparison endpoint and displayed on the dashboard. Almost any
error in fill application, lot matching, or mark-to-market breaks it. A typical
run reports a residual around `1e-12` — float noise.

Alongside it, `assert_invariants()` on the book checks that it is never crossed
across agents, that every level's cached quantity equals the live quantity in its
queue, and that no two orders share a sequence number.

---

## Results worth reading

From `scripts/demo_run.py`, seed 42, 1,000 steps:

- **In a calm market (σ = 0.12, no shock), the market maker earns the spread:**
  +233 PnL, 100% win rate across 190 closed round trips, 0.01% max drawdown. This
  is the economic sanity check on the whole model, and it is a test
  (`test_the_maker_earns_the_spread_in_a_calm_market`): if the maker cannot make
  money here, the quoting or the accounting is wrong regardless of what anything
  else says.
- **In a trending or shocked market the maker gets adversely selected** and
  momentum wins. In the run above the reference price fell 12% and the trend
  follower took the top spot; the maker's win rate stayed high (67%) while its PnL
  collapsed — it kept capturing the spread and kept getting run over on inventory.
  That combination is the signature of adverse selection.
- **Through the shock**, the maker's unrealized PnL goes from +4 to -136 in a
  single step, and the book's touch drops from 99.41/100.02 to 91.31/91.92. You can
  scrub the dashboard's book view across the shock step and watch it, because the
  book is replayed from the persisted event log.
- **Mean reversion loses money consistently**, and it should: GBM log returns are
  independent, so there is nothing to revert to. Its 8.6% win rate is the exit rule
  repeatedly flattening at a loss. That is a property of the price process, not
  evidence the strategy is bad — stated here rather than left for the comparison
  table to imply otherwise.

---

## Order book reconstruction

The in-memory book does not survive the request that created it, and pinning it
in a process-local cache would break with more than one worker. So the book is
rebuilt from the persisted order stream — **exactly**, because the stream is a
complete event log: submission step, priority sequence, and cancel sequence.

Getting this wrong was instructive. An earlier version applied all of a step's
cancels before that step's submissions. The resting book came out identical and
**half the executions vanished** — because within a step a taker often hits a
maker's stale quote before the maker's turn comes round to pull it. A
reconstruction that matches on shape while losing half the tape is the most
dangerous kind of wrong, and it is why cancels carry a sequence number and not
just a step.

---

## API

```
POST   /simulations                                   Create (price process + shocks)
GET    /simulations                                   List
GET    /simulations/{id}                              Detail
DELETE /simulations/{id}                              Delete (cascades)
POST   /simulations/{id}/agents                       Add agent (type + config)
GET    /simulations/{id}/agents                       List agents
POST   /simulations/{id}/run                          Run N steps
GET    /simulations/{id}/order-book/snapshot?step=    Book, replayed at any step
GET    /simulations/{id}/agents/{agent_id}/performance Per-step PnL / inventory series
GET    /simulations/{id}/comparison                   Sharpe, drawdown, PnL, conservation check
GET    /meta/agent-defaults                           Every tunable, with defaults
GET    /meta/engine-config                            Every formula and constant in force
GET    /health
```

Two design decisions in the API worth naming:

- **A misspelled agent config key is a 422, not a silently ignored default.** A run
  whose parameters are not what the caller asked for is not reproducible.
- **The resolved config and the engine constants are persisted with every run.**
  A stored Sharpe of 6.54 cannot be checked without knowing `capital_base` and
  `steps_per_year`, and neither is recoverable from the PnL series.

---

## Known limitations

Stated here rather than discovered by a reader:

- **The price process is synthetic.** Standard for strategy research; still a
  limitation. σ is not calibrated to any real instrument by default.
- **The market maker sees the true fair value.** A live maker infers it from order
  flow. This hands the maker a small informational edge over the directional
  agents. Building the inference model is a project of its own and would not
  change the microstructure mechanics this platform exists to demonstrate. The
  directional agents provably do *not* see it — `tests/test_no_lookahead.py`
  perturbs the field by 50% and asserts their decisions do not move.
- **Single instrument.** Deliberate scope. Multi-instrument correlation adds
  complexity without adding microstructure depth.
- **No agent learning.** Parameters are fixed for a run. Adaptive agents are V2.
- **No latency model.** Every agent acts on the same tick with no propagation
  delay. In real markets latency *is* the game for a market maker.
- **`create_all` instead of migrations.** The schema is append-only for V1;
  Alembic is the correct answer the moment a column changes shape.
- **Runs are synchronous.** A 20,000-step run holds an HTTP request open. A job
  queue is the answer past that scale; the step cap is the guard for now.

## V2

Multi-instrument correlated price processes · agents that adapt parameters from
realised performance · σ calibrated to real historical volatility · a latency
model with per-agent propagation delay · queue-position analytics for the maker.

---

## Interview questions this project is built to answer

1. Walk me through your matching engine — how does price-time priority work, and
   what happens to a resting order's queue position when it is partially filled?
2. How does the market maker manage inventory risk, and why does the skew
   subtract from both quotes instead of widening the spread?
3. Your momentum and mean-reversion agents read the same price series. What is the
   actual difference, and which one should win under GBM?
4. How do you compute Sharpe from a PnL series, and what is wrong with
   `(peak - trough) / peak` as a drawdown?
5. Walk me through what happens to each agent at the volatility shock.
6. How do you know your PnL accounting is correct?

---

## Licence

MIT — see [LICENSE](LICENSE). Not investment advice. Synthetic data. No live
trading.
