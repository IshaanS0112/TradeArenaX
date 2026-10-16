# TradeArena X

[![CI](https://github.com/IshaanS0112/TradeArenaX/actions/workflows/ci.yml/badge.svg)](https://github.com/IshaanS0112/TradeArenaX/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 18](https://img.shields.io/badge/react-18-61DAFB?logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/tests-540%20passing-brightgreen)](backend/tests)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**An event-driven market simulator: a real limit order book, agents that compete on
it under per-agent latency, and a statistics layer that refuses to let a backtest
flatter itself.**

A price-time-priority matching engine; five agent archetypes (inventory-aware market
making, momentum, mean reversion, uninformed noise flow, and an options maker that
hedges its delta into the same equity book); a microsecond event clock that gives
each agent its own feed delay and order flight time; adverse-selection measurement
that decomposes the spread a maker earns into what it keeps and what it gives back;
and Monte-Carlo ensembles with Deflated Sharpe Ratio and Probability of Backtest
Overfitting over parameter sweeps.

---

## What is real and what is simulated

| Component | Status |
|---|---|
| Limit order book, price-time priority matching | **Real.** Heap of price levels, per-level FIFO queues, partial fills, cancels, self-trade prevention. |
| Discrete-event clock and per-agent latency | **Real.** Microsecond event queue, stale feeds, order flight time, genuine fill-vs-cancel races. |
| All five agent strategies | **Real.** Every signal computed from observable market data only. |
| PnL accounting | **Real.** FIFO lot matching, position flips, mark-to-market on open inventory. |
| Risk and performance metrics | **Real.** Sharpe, Sortino, max drawdown, win rate, inventory risk scoring. |
| Spread decomposition and adverse selection | **Real.** Effective / realised / impact, measured off the trade tape. |
| Option pricing, Greeks, implied vol, SVI | **Real.** Black-Scholes closed form, bracketed-Brent inversion, arbitrage-checked surface fits. |
| Bootstrap CIs, Deflated Sharpe, PBO | **Real.** Stationary bootstrap, Lo (2002) cross-check, combinatorially symmetric cross-validation. |
| **The price process** | **Synthetic.** Geometric Brownian motion with configurable jump discontinuities. |

There is no live market data feed, no broker integration, and no capital at risk.
This is a strategy research and education tool — the standard setting for this kind
of work, since quant research runs on generated or historical paths rather than
live capital. Nothing here is investment advice.

A synthetic path is a deliberate choice rather than a shortcut. The value of the
project is in the microstructure mechanics: how a matching engine assigns priority,
how a maker's quotes lean against inventory, how a cancel that leaves 300 µs too
late still gets run over, and how FIFO lot matching handles a position flip. None of
that becomes more real with a live feed — it becomes harder to test and impossible
to reproduce. What a generated path buys instead is reproducibility (same seed, same
result, exactly, across processes) and controlled stress (a −15% jump at step 400
with a 4× volatility regime behind it cannot be scheduled in a real market).

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
without a database server. The schema is managed by Alembic and the migrations run
on application startup, so a fresh database needs no separate step.

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
cd backend && python -m pytest          # 540 passing, 13 skipped
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

Four details a naive implementation gets wrong:

- **Prices are integers.** A level is `round(price / tick_size)`. Keyed on floats,
  `100.10 * 3 / 3` and `100.10` land on different levels — two prices no trader can
  distinguish, which the matcher then refuses to cross.
- **Time priority uses a sequence counter, not a clock.** Wall-clock timestamps tie
  constantly within a simulation step, and a tie in the priority key means the
  matching order is whatever the container happened to do.
- **Quantity arithmetic has to snap to zero.** Subtracting partial fills from a
  level total leaves float dust — `1.78e-15` is not zero, so the level survives
  pruning and `best_bid` starts reporting a price where nothing rests. Left alone
  this presents much later as a crossed book, with no trace of the cause.
- **Self-trade prevention.** A market maker quotes both sides; the moment its spread
  inverts it can trade with itself and book a fictional profit. Three policies are
  implemented, and the crossed-book invariant recognises that a same-agent cross
  under *skip* is legitimate while any cross across agents is a bug.

Execution happens at the **resting** order's price — the passive side set the terms.
Filling at the incoming limit would hand the aggressor a rebate it never earned.

### The event engine — a clock instead of a loop

The original engine stepped every agent in lockstep: everyone saw the same book at
the same instant and their orders landed in list order. That is a fiction, and it is
the fiction that makes a simulated market maker look good.

`EventDrivenEngine` replaces the loop with a priority queue over
`(timestamp_us, sequence)`, where `sequence` is a **tuple carrying the event's causal
path** — agent A's wake is `(step, 1, 0)` and the orders that wake produces are
`(step, 1, 0, 0)`, `(step, 1, 0, 1)`. Lexicographic ordering on that tuple means a
cause always precedes its effect, no matter what timestamps the latency model hands
out. Six event kinds flow through it: `PRICE_TICK`, `AGENT_WAKE`, `ORDER_ARRIVAL`,
`CANCEL_ARRIVAL`, `FILL_NOTIFY`, `STEP_CLOSE`.

Each agent carries a `LatencyProfile`:

| Field | Meaning | Effect |
|---|---|---|
| `latency_in_us` | feed staleness | the agent decides on the last publication **at or before** `wake − latency_in`, so a slow feed quantises to whole publications |
| `latency_out_us` | order flight time | continuous, so two agents reacting to the same tick arrive in latency order |
| `latency_jitter_us` | Gaussian noise on the outbound leg | clamped at zero; this is what turns a deterministic ordering into a race |

The keystone test is **zero-latency equivalence**: with every profile at zero, the
event engine reproduces the old step loop exactly — step records, fills, orders, PnL
and inventory, over 300 steps, with and without a shock. Without that, "the engine
got more realistic" is indistinguishable from "the engine changed".

What the clock buys is the thing a step loop cannot express: a maker sees a tick,
decides to pull its quote, and the cancel arrives 300 µs after a faster taker's order
has already eaten it. Those are recorded as `LatencyRace` rows with the margin in
microseconds, and surfaced at `/latency-races`.

### Adverse selection — decomposing the spread

A maker's quoted spread is not what it keeps. Per fill, from the **passive side**:

```
effective = D · (P − M_t)          what the maker charged at the moment of the trade
realised  = D · (P − M_{t+Δ})      what it still had Δ steps later
impact    = D · (M_{t+Δ} − M_t)    what the trade told the market, and cost the maker
```

with `D = +1` for the maker and `−1` for the taker, and `effective = realised +
impact` as an identity the tests assert rather than a claim. Measured at horizons of
1, 5 and 20 steps, per agent and market-wide, at `/microstructure`.

This is the number that makes the maker's story legible: in a trending market its win
rate stays high and its PnL collapses, and the decomposition says exactly why — the
effective half-spread is unchanged and the impact term has eaten all of it.

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

### Momentum, mean reversion and noise

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

The noise trader is uninformed flow: it crosses the spread at random intensity with
no view at all. It exists because a book with only informed agents is not a market —
without it the maker has no benign flow to earn the spread from, and the adverse
selection measurement has nothing to contrast against.

### Derivatives — pricing, Greeks, and a hedged book

`services/derivatives/` is Black-Scholes in closed form with analytic delta, gamma,
vega, theta, rho, vanna and vomma, guarded at `τ → 0` and `σ → 0` where the
textbook expressions divide by zero. Both raw and quoted units are carried
(`vega_per_point`, `theta_per_day`) because mixing them is the most common source of
a Greeks table that is wrong by a factor of 100.

**Implied volatility is inverted by bracketed Brent, not Newton**, because vega
collapses in the wings and Newton walks off. More importantly, the inversion is
allowed to fail: for a deep-ITM quote the price is bit-identical across a wide range
of volatilities, so the function measures the **interval of volatilities consistent
with the quote** and returns `None` with that interval as the reason when it exceeds
half a vol point. A number returned there would be an artefact of float spacing.

`fit_svi_slice` fits the raw SVI total-variance parameterisation

```
w(k) = a + b · [ρ(k − m) + √((k − m)² + σ²)]
```

by coordinate descent from six restarts, and `butterfly_arbitrage` /
`calendar_arbitrage` check the Gatheral conditions on the result, reporting each
violation's location and magnitude rather than a bare boolean.

`OptionsMarketMakerAgent` ties it to the order book: it quotes a fixed strip, widens
with vega and gamma, takes simulated client flow, computes portfolio Greeks each
step, and hedges delta **into the same equity book every other agent is trading**.
The hedge band is Whalley–Wilmott,

```
band = (3/2 · cost · S · Γ² / γ)^(1/3)
```

so a breach hedges back to the edge of the band rather than to zero — hedging to zero
pays the spread for nothing. Option premium is deliberately kept **out** of the
equity position tracker: folding it in would break the conservation invariant that
every other result in the project rests on.

### Statistics — not letting the backtest flatter itself

`services/statistics.py` is dependency-free (no SciPy; `normal_ppf` is the Acklam
rational approximation with one Halley refinement) and holds the machinery for
deciding whether a result means anything:

- **Stationary bootstrap** (Politis–Romano, geometric blocks) for confidence
  intervals *within* a return series, because returns are not IID and resampling them
  one at a time destroys the autocorrelation that makes a Sharpe ratio uncertain.
- **IID bootstrap** across independent ensemble paths, where the independence is real.
- **Lo (2002) analytic standard error** as a cross-check, with a flag raised when the
  two disagree by more than 25% — a disagreement means the block length is wrong, and
  silently trusting either one is how a confidence interval becomes decorative.
- **Deflated Sharpe Ratio** (Bailey & López de Prado): the probability that an
  observed Sharpe survives the number of trials it took to find it, given the
  skewness and kurtosis of the returns.
- **Probability of Backtest Overfitting** via Combinatorially Symmetric
  Cross-Validation: over every balanced split of the path set, how often the
  in-sample-best parameter cell lands in the bottom half out of sample.
- **Purged k-fold with an embargo**, so a fold boundary cannot leak.

Ensembles (`POST /ensembles`) run N paths at `base_seed + i` across a process pool and
report distributions, histograms and bootstrap CIs per agent. Sweeps
(`POST /sweeps`) take a parameter grid, split the paths in and out of sample, and
return the PBO, the best cell, and the **centroid of the top decile** — the parameters
you would actually deploy, which are not the argmax.

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

This runs on **every simulation**, not only in tests — including every path of every
ensemble. The residual is returned by the comparison endpoint and displayed on the
dashboard; a typical run reports about `1e-12`. Almost any error in fill application,
lot matching or mark-to-market breaks it.

Alongside it, `assert_invariants()` checks that the book is never crossed across
agents, that every level's cached quantity equals the live quantity in its queue,
and that no two orders share a sequence number.

**Reproducibility is tested across processes, not just within one.** An agent seeding
itself from `hash(agent_id)` is reproducible in a single run and silently different
in the next one, because Python salts string hashing per process — which quietly
voids the project's central "same seed, same result" promise. Seeds are derived with
`zlib.crc32`, and a test runs the same simulation in subprocesses under three
different `PYTHONHASHSEED` values and asserts the results are identical.

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
  the signature of adverse selection, and the spread decomposition puts a number on
  it.
- **Through the shock**, the maker's unrealized PnL moves from +4 to −136 in one
  step and the touch drops from 99.41/100.02 to 91.31/91.92.
- **Mean reversion loses consistently**, as it should: GBM log returns are
  independent, so there is nothing to revert to. Its 8.6% win rate is the exit rule
  flattening at a loss. This is a property of the price process, not evidence about
  the strategy.

### Three results that contradicted the design, and were kept anyway

- **The microprice does not beat the mid here.** Queue imbalance correlates −0.06 to
  −0.12 with the next mid move, with the *wrong* sign, so neither the weighted
  microprice nor the fitted conditional-expectation estimator forecasts better than
  the mid. The cause is structural: almost all resting size belongs to a single
  inventory-skewing maker, so imbalance measures that maker's position rather than
  the market's intent. Two tests pin this down rather than hiding it, and the module
  ships reporting the finding.
- **Some quotes identify no implied volatility at all**, and the inversion says so
  instead of returning a number.
- **`hash()` is not stable across processes**, which had already broken the
  reproducibility guarantee silently before a cross-process test caught it.

A simulator that only reports the results it was designed to produce is not
measuring anything.

---

## Architecture

```
React + TS + Tailwind (nginx)      FastAPI                PostgreSQL
        │  /api same-origin proxy     │  SQLAlchemy 2.0       │  Alembic
        └────────────────────────────►│──────────────────────►│
                                      │
                   ┌──────────────────┴──────────────────┐
                   │  SimulationEngine ◄─ EventDrivenEngine │
                   └──────────────────┬──────────────────┘
       ┌───────────┬────────────┬─────┴──────┬────────────┬───────────┐
       ▼           ▼            ▼            ▼            ▼           ▼
   OrderBook   Agents (5)  PositionTracker PriceProcess Latency   Derivatives
   (matching)  (strategy)  (FIFO PnL)     (GBM+shocks) (clock)   (BS/IV/SVI)
                                      │
                   ┌──────────────────┴──────────────────┐
                   ▼                  ▼                  ▼
             microstructure       ensembles           statistics
             (spread decomp)    (Monte Carlo)      (DSR / PBO / bootstrap)
```

`SimulationEngine` has no database dependency; `run_service.py` is the only module
that knows both the engine and the ORM. `EventDrivenEngine` subclasses it, so the
latency layer is additive and the zero-latency path is provably the original one.
Full detail, including the step ordering that prevents look-ahead bias and the
reasoning behind each schema column, is in [docs/architecture.md](docs/architecture.md).

The frontend is a **module registry**: each panel declares what data it needs and the
shell owns all fetching, so adding a visualisation is one entry in
`src/modules/registry.ts`. Design tokens live as CSS custom properties with
`-rgb` channel variants, which is what lets Tailwind's `/70` opacity syntax work
against them. The 3D liquidity surface is a single subdivided plane displaced in a
vertex shader from a `DataTexture`, rendered on `frameloop="demand"` and
code-split into its own chunk so three.js never lands in the main bundle.

## API

```
POST   /simulations                                     Create (price process + shocks)
GET    /simulations                                     List
GET    /simulations/{id}                                Detail
DELETE /simulations/{id}                                Delete (cascades)
POST   /simulations/{id}/agents                         Add agent (type + config + latency)
GET    /simulations/{id}/agents                         List agents
POST   /simulations/{id}/run                            Run N steps
GET    /simulations/{id}/order-book/snapshot?step=      Book, replayed at any step
GET    /simulations/{id}/agents/{agent_id}/performance  Per-step PnL / inventory
GET    /simulations/{id}/comparison                     Metrics + conservation check
GET    /simulations/{id}/liquidity-surface              Depth over (time × price), packed grid
GET    /simulations/{id}/microstructure                 Effective / realised / impact per agent
GET    /simulations/{id}/latency-races                  Fills that beat their own cancel
GET    /simulations/{id}/microprice                     Microprice vs mid, with forecast RMSE
GET    /simulations/{id}/greeks/attribution             Gamma / theta / vega / slippage PnL

POST   /ensembles                                       Run N paths (202, runs in background)
GET    /ensembles                                       List
GET    /ensembles/{id}                                  Status + aggregates
GET    /ensembles/{id}/distribution                     Histograms + bootstrap CIs per agent
GET    /ensembles/{id}/paths                            Per-path results
DELETE /ensembles/{id}                                  Delete

POST   /sweeps                                          Parameter grid (202, runs in background)
GET    /sweeps                                          List
GET    /sweeps/{id}                                     Cells + PBO + robust centroid
GET    /sweeps/{id}/surface                             2D slice for plotting
DELETE /sweeps/{id}                                     Delete

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
  default. Order arrivals are agent-driven rather than drawn from a Hawkes or
  queue-reactive model, so clustering in the flow is an emergent property of the
  agents and not a modelled one.
- The market maker observes true fair value; a live maker infers it from order flow.
  This is a small informational edge over the directional agents. Those agents
  provably do not see it — `tests/test_no_lookahead.py` perturbs the field by 50% and
  asserts their decisions do not change.
- Single instrument. No cross-asset correlation, no pairs trading, and no agent
  learning — parameters are fixed per run rather than adapted from realised
  performance.
- The options layer prices on a flat configured volatility; the SVI machinery fits
  and checks surfaces but does not yet feed the maker's own quotes.
- Latency is per-agent and stochastic, but the exchange itself is instantaneous —
  there is no matching-engine queue delay or market-data publication cost.
- Ensembles and sweeps run on FastAPI background tasks rather than a real job queue.
  Correct for a single process; Redis or ARQ is the answer the moment there is more
  than one.
- No streaming. The dashboard polls; a live book belongs on a WebSocket.

## Roadmap

Full Avellaneda–Stoikov with an estimated fill intensity (`A`, `k`) · Hawkes and
queue-reactive order arrival · multi-asset correlated processes and a pairs agent ·
agents that adapt parameters online · Almgren–Chriss execution scheduling · VPIN and
Kyle's lambda as toxicity measures · WebSocket streaming and a Redis-backed job
queue.

## Licence

MIT — see [LICENSE](LICENSE).

**Not investment advice.** This is a research and education tool running on
synthetic data, with no connection to any market data feed, exchange or broker.
See [DISCLAIMER.md](DISCLAIMER.md).
