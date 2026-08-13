# TradeArena X

[![CI](https://github.com/IshaanS0112/TradeArenaX/actions/workflows/ci.yml/badge.svg)](https://github.com/IshaanS0112/TradeArenaX/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 18](https://img.shields.io/badge/react-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/tests-210%20passing-brightgreen)](backend/tests)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Market-making and trading strategy simulation on a real limit order book.**

A price-time-priority matching engine, three competing agent archetypes
(inventory-aware market making, momentum, mean reversion), and a comparison layer
reporting Sharpe ratio, max drawdown, win rate and inventory risk — under normal
conditions and under injected volatility shocks.

---

## What is real and what is simulated

| Component | Status |
|---|---|
| Limit order book, price-time priority matching | **Real.** Heap of price levels, per-level FIFO queues, partial fills, cancels, self-trade prevention. |
| All three agent strategies | **Real.** Every signal computed from observable market data only. |
| PnL accounting | **Real.** FIFO lot matching, position flips, mark-to-market on open inventory. |
| Risk and performance metrics | **Real.** Sharpe, Sortino, max drawdown, win rate, inventory risk scoring. |
| **The price process** | **Synthetic.** Geometric Brownian motion with configurable jump discontinuities. |

There is no live market data feed, no broker integration, and no capital at risk.
This is a strategy research and education tool — the standard setting for this kind
of work, since quant research runs on generated or historical paths rather than
live capital. Nothing here is investment advice.

A synthetic path is a deliberate choice rather than a shortcut. The value of the
project is in the microstructure mechanics: how a matching engine assigns priority,
how a maker's quotes lean against inventory, how FIFO lot matching handles a
position flip, and how those interact when the price gaps 8% in one step. None of
that becomes more real with a live feed — it becomes harder to test and impossible
to reproduce. What a generated path buys instead is reproducibility (same seed,
same result, exactly) and controlled stress (a -15% jump at step 400 with a 4×
volatility regime behind it cannot be scheduled in a real market).

---

## Quick start

```bash
docker compose up --build
```

| | |
|---|---|
| Dashboard | http://localhost:5173 |
| API documentation | http://localhost:8000/docs |
| Every formula in force | http://localhost:8000/meta/engine-config |

### Without Docker

Requires Python 3.10–3.12 and Node 20+.

```bash
# Backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-test.txt
export DATABASE_URL="sqlite:///./tradearenax.db"
uvicorn app.main:app --reload

# Frontend, in a second shell
cd frontend && npm install && npm run dev
```

`DATABASE_URL` defaults to PostgreSQL; the SQLite URL above runs the whole system
without a database server.

### Running the engine directly

No database, no HTTP layer — the fastest way to check that a change to the
matching engine or the agents has not broken the economics:

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
```

### Tests

```bash
cd backend && python -m pytest          # 210 tests
docker compose run --rm --build tests   # or, in a container

cd frontend && npm run typecheck && npm run build
```

---

## Mechanics

### Limit order book — price-time priority

Two heaps of *price levels*, not of orders:

```
bids: max-heap of int ticks, each level a FIFO deque
asks: min-heap of int ticks, each level a FIFO deque
```

Price priority comes from the heap; time priority from the deque. Per-level FIFOs
are what make time priority survive a partial fill — a resting order that is half
filled keeps the head of its queue instead of being re-inserted behind orders that
arrived after it.

Three details a naive implementation gets wrong:

- **Prices are integers.** A level is `round(price / tick_size)`. Keyed on floats,
  `100.10 * 3 / 3` and `100.10` land on different levels — two prices no trader can
  distinguish, which the matcher then refuses to cross.
- **Time priority uses a sequence counter, not a clock.** Wall-clock timestamps tie
  constantly within a simulation step, and a tie in the priority key means the
  matching order is whatever the container happened to do.
- **Self-trade prevention.** A market maker quotes both sides; the moment its spread
  inverts it can trade with itself and book a fictional profit. Three policies are
  implemented, and the crossed-book invariant recognises that a same-agent cross
  under *skip* is legitimate while any cross across agents is a bug.

Execution happens at the **resting** order's price — the passive side set the terms.
Filling at the incoming limit would hand the aggressor a rebate it never earned.

### Market maker — inventory-aware quoting

```
skew = k · inventory
bid  = fair_value − half_spread − skew
ask  = fair_value + half_spread − skew
```

The skew subtracts from **both** quotes. Long inventory shifts the whole quote down
— the bid gets less attractive to sellers, the ask more attractive to buyers — so
the flow it attracts reduces the position. The width is unchanged: width responds to
*volatility*, the shift responds to *inventory*, and the two are frequently
conflated.

This is the simplified Avellaneda–Stoikov form: the linear inventory term with a
fixed base width. The full result derives both the reservation-price shift and the
optimal half-spread from a utility function with a risk-aversion parameter and a
finite horizon.

Two additions beyond the base formula, both of which change outcomes: volatility
widening capped at a multiple of the base spread, and a hard one-sided cutoff at 85%
of the inventory limit — skew is a price incentive, and a sufficiently
one-directional market will pay the skew and keep filling you.

### Momentum and mean reversion

```
momentum:       (P_t − P_{t−N}) / P_{t−N}  >  threshold  →  BUY
mean reversion: (P_t − MA_w) / SD_w        >  threshold  →  SELL
```

Momentum trades *with* the deviation, mean reversion *against* it. Both read the
identical series and disagree about whether a move is information or noise; a test
feeds one ramp to both and asserts they take opposite sides.

Both compute signals from the **observable mid-price series** — what any participant
could read off the public book — not from the latent price path and not from the
trade tape.

### PnL — FIFO lot matching

Realized PnL uses FIFO lot matching, not `sum(sells) − sum(buys)`. That shortcut is
cash flow, not realized PnL, and it reports a fictional loss whenever an agent is net
long.

The case that makes it non-trivial is a **position flip**: long 10 at 100, then sell
25 at 105 closes 10 long for +50 and *opens* 15 short at 105. Handled as one signed
number, the opening leg gets booked as if it had closed something.

### Metrics

**Sharpe needs a return series, and PnL is not one.** `(mean − rf) / sd` is
dimensionless only if the numerator is a return; PnL is currency, and its ratio
scales with position size. The PnL series is converted to returns against an equity
curve (`capital_base + PnL`) and annualised by `sqrt(steps_per_year)`. Both the
annualised and per-step figures are reported, and a run shorter than one trading day
is flagged as over-extrapolated.

**Max drawdown as a percentage of peak PnL breaks when PnL is negative.** Peak −50,
trough −200 gives −300%: a negative drawdown, which is not a quantity. Drawdown is
computed on the equity curve, whose peak is bounded below by the capital base.

Zero-dispersion Sharpe returns `None`, not infinity. An agent with no closed round
trips has `None` win rate, not 0%.

### Volatility shocks

`dS = μS·dt + σS·dW`, integrated in its exact log form:

```
S_{t+1} = S_t · exp((μ − σ²/2)·dt + σ·√dt·Z)
```

not an Euler step, which goes negative as soon as `σ√dt·Z < −1`. A shock is a jump
discontinuity multiplied onto that step's diffusion, and optionally leaves elevated
volatility that decays with a configurable half-life — volatility clustering, which
is what makes a shock a stress test rather than one bad tick.

---

## Correctness

Every trade moves value between two participants; nothing enters or leaves except
fees. So:

```
Σ(all agents' total PnL) + fees collected == 0
```

This runs on **every simulation**, not only in tests. The residual is returned by
the comparison endpoint and displayed on the dashboard; a typical run reports about
`1e-12`. Almost any error in fill application, lot matching or mark-to-market breaks
it.

Alongside it, `assert_invariants()` checks that the book is never crossed across
agents, that every level's cached quantity equals the live quantity in its queue,
and that no two orders share a sequence number.

The book is also reconstructible: `services/book_replay.py` rebuilds the state at
any historical step by replaying the persisted order stream — submissions and
cancels interleaved by sequence number — through the same matching engine, and a
test asserts the replay reproduces both the live book and the live trade count.

---

## Results

Seed 42, 1,000 steps:

- **In a calm market (σ = 0.12, no shock) the maker earns the spread:** +233 PnL,
  100% win rate across 190 closed round trips, 0.01% max drawdown. This is the
  economic sanity check on the model and it is enforced as a test — if the maker
  cannot make money here, the quoting or the accounting is wrong.
- **In a trending or shocked market the maker is adversely selected** and momentum
  wins. Above, the reference price fell 12%; the maker's win rate stayed at 67%
  while its PnL collapsed — capturing the spread and being run over on inventory is
  the signature of adverse selection.
- **Through the shock**, the maker's unrealized PnL moves from +4 to −136 in one
  step and the touch drops from 99.41/100.02 to 91.31/91.92.
- **Mean reversion loses consistently**, as it should: GBM log returns are
  independent, so there is nothing to revert to. Its 8.6% win rate is the exit rule
  flattening at a loss. This is a property of the price process, not evidence about
  the strategy.

---

## Architecture

```
React + TS + Tailwind (nginx)      FastAPI                PostgreSQL
        │  /api same-origin proxy     │  SQLAlchemy 2.0       │
        └────────────────────────────►│──────────────────────►│
                                      │
                        ┌─────────────┴─────────────┐
                        │      SimulationEngine      │
                        └─────────────┬─────────────┘
                 ┌────────────┬───────┴──────┬──────────────┐
                 ▼            ▼              ▼              ▼
            OrderBook     Agents (3)   PositionTracker  PriceProcess
            (matching)    (strategy)   (FIFO PnL)       (GBM + shocks)
```

`SimulationEngine` has no database dependency; `run_service.py` is the only module
that knows both the engine and the ORM. Full detail, including the step ordering
that prevents look-ahead bias and the reasoning behind each schema column, is in
[docs/architecture.md](docs/architecture.md).

## API

```
POST   /simulations                                     Create (price process + shocks)
GET    /simulations                                     List
GET    /simulations/{id}                                Detail
DELETE /simulations/{id}                                Delete (cascades)
POST   /simulations/{id}/agents                         Add agent (type + config)
POST   /simulations/{id}/run                            Run N steps
GET    /simulations/{id}/order-book/snapshot?step=      Book, replayed at any step
GET    /simulations/{id}/agents/{agent_id}/performance  Per-step PnL / inventory
GET    /simulations/{id}/comparison                     Metrics + conservation check
GET    /meta/agent-defaults                             Every tunable, with defaults
GET    /meta/engine-config                              Every formula and constant
```

A misspelled agent config key returns 422 rather than silently falling back to a
default, and the resolved config plus the engine constants are persisted with every
run — a stored Sharpe cannot be verified without knowing `capital_base` and
`steps_per_year`, and neither is recoverable from the PnL series.

---

## Limitations

- The price process is synthetic, and σ is not calibrated to any real instrument by
  default.
- The market maker observes true fair value; a live maker infers it from order flow.
  This is a small informational edge over the directional agents. Those agents
  provably do not see it — `tests/test_no_lookahead.py` perturbs the field by 50% and
  asserts their decisions do not change.
- Single instrument, no agent learning, no latency model. Every agent acts on the
  same tick with no propagation delay; in real markets latency is the game for a
  market maker.
- `create_all` rather than migrations. Correct while the schema is append-only;
  Alembic is the answer the moment a column changes shape.
- Runs are synchronous — a 20,000-step run holds an HTTP request open. A job queue is
  the answer past that scale; the step cap is the current guard.

## Roadmap

Multi-instrument correlated price processes · agents that adapt parameters from
realised performance · σ calibrated to historical volatility · a latency model with
per-agent propagation delay · queue-position analytics for the maker.

## Licence

MIT — see [LICENSE](LICENSE). Not investment advice. Synthetic data. No live trading.
