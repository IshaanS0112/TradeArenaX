# TradeArena X — Product and Technical Specification

> **This is reverse-documentation of a system that already exists.** Everything below
> describes shipped, tested behaviour, not a plan. Where something is deliberately absent
> it is listed under *Non-goals* or *Not built* rather than quietly omitted.
>
> Written for a reviewer who needs to judge the system without reading the engine source.
> For the reasoning behind individual design decisions, see
> [`architecture.md`](architecture.md). For a tour, see the [README](../README.md).

| | |
|---|---|
| **Status** | Shipped. 540 tests passing, 13 skipped. |
| **Scale** | Backend 7,656 LOC · tests 4,552 LOC · frontend 3,917 LOC |
| **Stack** | FastAPI · SQLAlchemy 2.0 · Alembic · PostgreSQL / SQLite · React 18 · TypeScript · Tailwind · Vite |
| **Deployment** | `docker compose up --build` — four services |

---

# Part 1 — Product Requirements

## 1.1 Overview

**One line.** A market simulator that measures *why* a trading strategy made or lost money,
and reports whether the answer is statistically meaningful.

**The problem.** Backtests flatter their authors. Three failure modes recur, and a typical
backtesting tool reports none of them:

1. **No execution realism.** Orders fill at the price you wanted, instantly. Real quotes are
   picked off by faster participants before a cancel lands.
2. **No attribution.** A market maker's PnL is reported as one number. Whether it earned the
   spread or was run over on inventory is invisible, and those have opposite implications.
3. **No multiple-testing correction.** Try 200 parameter combinations and the best one will
   look excellent whether or not any edge exists.

**Target users.** Analysts and engineers evaluating trading logic — quantitative researchers,
data and business analysts producing execution-quality reporting, and engineers who need a
correct order book to build against. Also: anyone assessing the author's engineering.

**Proposed solution.** A discrete-event simulator where each participant has its own latency,
every trade is decomposed into what the maker kept versus what informed flow took back, and
parameter searches report a Deflated Sharpe Ratio and a Probability of Backtest Overfitting
alongside the headline number.

**Product goal.** A reviewer should be able to state, from the dashboard alone and within five
minutes, why each desk ended where it did — and should be able to export the rows behind any
claim.

## 1.2 User flows

### Primary — evaluate a configuration

```
Configure run          name, seed, volatility, drift, scheduled shocks
   ↓
Add desks              up to 12; type + strategy config + latency profile
   ↓
Run N steps            synchronous; capped at 20,000
   ↓
Read the workspace     four preset layouts; shared step scrubber
   ↓
Export                 CSV per panel, for a spreadsheet or a notebook
```

The step scrubber is shared state: moving it re-points every point-in-time panel at once, so
the book, the ladder and the tape always describe the same instant.

### Secondary — is this result real?

```
Ensemble      N seeded paths in a process pool  →  distributions, bootstrap CIs,
                                                   fraction of paths profitable
Sweep         parameter grid, paths split in/out of sample
                 →  PBO, best cell, and the centroid of the top decile
                    (the parameters you would actually deploy, which are not the argmax)
```

Both are 202-Accepted and run in the background; the client polls status.

### Failure flows

| Situation | Behaviour |
|---|---|
| Misspelled agent config key | `422` before any work starts, naming the unknown key |
| Shock scheduled past the end of the run | `422` at validation |
| Metrics requested before a run completes | `409 Conflict` naming the fix, not an empty `200` |
| Unknown simulation / agent | `404` |
| Agent cannot liquidate — no resting liquidity | Run continues; warning surfaced on the dashboard |
| A panel's endpoint fails | Panel renders a `role="alert"` error with the endpoint and a Retry; the rest of the dashboard keeps working |
| A quantity has no meaningful value | Printed as `—`, never as `0` |

That last row is a product rule, not a formatting detail. An agent with no closed round trips
has an *unknown* win rate, not a 0% one, and the two must not look alike.

## 1.3 Features

### Shipped

| Area | Capability |
|---|---|
| Matching | Price-time priority, integer tick grid, per-level FIFO, partial fills, lazy cancels, three self-trade policies, execution at the resting price |
| Timing | Discrete-event clock; per-agent feed delay, order flight time, Gaussian jitter; contested fills recorded with microsecond margins |
| Desks | Market maker (inventory skew), momentum, mean reversion, noise flow, options maker (delta-hedged) |
| Accounting | FIFO lot matching through position flips; Sharpe on an equity curve; drawdown that stays non-negative |
| Attribution | Effective / realised / impact decomposition per fill, at 1, 5 and 20 steps, per agent and market-wide |
| Derivatives | Black-Scholes + analytic Greeks, implied vol by bracketed Brent, SVI fits with butterfly and calendar checks |
| Statistics | Stationary and IID bootstrap, Lo (2002) SEs, Deflated Sharpe, PBO via CSCV, purged k-fold with embargo |
| Scale-out | Ensembles and sweeps across a process pool |
| Dashboard | 14 panels, four presets, shareable layout in the URL, per-panel CSV export, 3D liquidity surface |
| Reproducibility | Same seed, same result — enforced *across processes*, not just within one |

### Next

- Dedicated ensemble and sweep pages. The endpoints exist and are tested; there is no UI.
- WebSocket streaming to replace dashboard polling.
- Full Avellaneda–Stoikov with an estimated fill intensity (`A`, `k`).
- SVI feeding the options maker's own quotes, instead of a flat configured volatility.

### Future

Hawkes and queue-reactive order arrival · multi-asset correlated processes and a pairs desk ·
online parameter adaptation · Almgren–Chriss execution scheduling · VPIN and Kyle's lambda ·
a reinforcement-learning benchmark desk.

### Deliberately not built

| Not built | Why |
|---|---|
| Live market data feed | The value is in microstructure mechanics, which a live feed makes harder to test and impossible to reproduce |
| Broker integration, order routing | Out of scope and a different risk class entirely |
| User accounts, multi-tenancy | Single-operator research tool; auth would be ceremony |
| A charting library | Four polylines over a shared x-axis does not justify a canvas renderer and a theming system |
| SciPy | The statistics layer needs seven functions; `normal_ppf` is 30 lines of Acklam |

## 1.4 Requirements

### Functional

1. A run is reproducible from `(seed, config)` alone, byte-identical, in any process.
2. Every trade must conserve value: `Σ agent PnL + fees == 0`, checked on **every** run.
3. The book is never crossed across agents; a same-agent cross under `SKIP` is legitimate.
4. An unknown configuration key is rejected before work begins, never silently defaulted.
5. The resolved config and engine constants are persisted with every run — a stored Sharpe is
   unverifiable without `capital_base` and `steps_per_year`, and neither is recoverable from
   the PnL series.
6. Book state at any historical step is reconstructible by replaying the persisted order stream.
7. Directional agents must not observe the latent price path.
8. At zero latency the event engine must reproduce the original step loop exactly.

### Non-functional

| Property | Target | Actual |
|---|---|---|
| Run throughput | — | ~1,000 steps × 5 desks in well under a second |
| Conservation residual | `< 1e-6` | `~1e-13` typical |
| Test suite | Fast enough to run on every save | ~15 s, 540 tests |
| Frontend main bundle | three.js excluded | 269 kB (87 kB gzip); three.js split into its own chunk |
| Database | Must run with no server | SQLite and PostgreSQL, same code, dialect variants |
| Accessibility | Keyboard-reachable analytics | Focus rings, labelled controls, keyboard chart cursor, `role="alert"` errors |

### Edge cases handled

- Zero-dispersion Sharpe returns `None`, not infinity.
- Negative PnL throughout: drawdown computed on the equity curve, so it cannot go negative.
- A level drained by repeated partial fills leaves float dust; quantities snap to zero at
  `1e-9` or the level becomes a phantom touch.
- Deep-ITM option quotes identify no implied volatility; the inversion returns `None` with the
  interval of consistent volatilities instead of an artefact of float spacing.
- A one-sided or empty book has no mid; unrealized PnL falls back to the last trade or the
  reference price, and the step is counted and reported.
- Runs shorter than one trading day flag their annualised Sharpe as over-extrapolated.

### Non-goals

Live trading · investment advice · calibration to any specific real instrument · sub-microsecond
performance engineering · horizontal scale beyond one machine's process pool.

### Success metrics

1. Conservation residual stays at float noise on every run. *(Met: ~1e-13.)*
2. The engine's behavioural claims are each pinned by a test. *(Met: 540 tests.)*
3. A reviewer can explain each desk's outcome from the dashboard alone.
4. Measured results are reported even when they contradict the design. *(Met: the microprice
   does not beat the mid here, and two tests say so.)*

---

# Part 2 — Technical Requirements

## 2.1 Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  React 18 + TypeScript + Tailwind (nginx in production)          │
│  Module registry: panels declare data deps, the shell fetches     │
└───────────────────────────┬──────────────────────────────────────┘
                            │  /api, same-origin proxy
┌───────────────────────────▼──────────────────────────────────────┐
│  FastAPI — routers: simulations · ensembles · sweeps · meta       │
│  Pydantic contracts; 422 on any unknown key                       │
└───────────────────────────┬──────────────────────────────────────┘
                            │  run_service.py — the ONLY module that
                            │  knows both the engine and the ORM
┌───────────────────────────▼──────────────────────────────────────┐
│  SimulationEngine ◄── EventDrivenEngine (subclass, additive)      │
│    OrderBook · Agents(5) · PositionTracker · PriceProcess         │
│    LatencyProfile · derivatives/ · microstructure · statistics    │
└───────────────────────────┬──────────────────────────────────────┘
                            │  SQLAlchemy 2.0 + Alembic
┌───────────────────────────▼──────────────────────────────────────┐
│  PostgreSQL 16  (or SQLite — JSONB/UUID as dialect variants)      │
└──────────────────────────────────────────────────────────────────┘
```

**Responsibilities.**

- **Frontend** — rendering and layout only. No business logic, no derived metrics: anything a
  reviewer might quote is computed server-side so the API and the screen cannot disagree.
- **Backend** — validation, orchestration, persistence, measurement.
- **Engine** — pure. No database dependency, importable from a script or a notebook. This is
  what makes `scripts/demo_run.py` possible and the test suite fast.
- **Database** — durable record of runs and the order stream the book is replayed from.

**Third-party integrations: none.** No external APIs, no telemetry, no auth provider, no
market data vendor. The dependency list is the framework, the ORM, NumPy, and three.js.

## 2.2 Database

Seven tables. `JsonBlob` is JSONB on PostgreSQL and JSON on SQLite; `UUIDStr` likewise.

| Table | Key fields | Notes |
|---|---|---|
| `simulations` | `id` (uuid), `name`, `duration_steps`, `status`, `price_process_config`, `volatility_shock_config`, `engine_config`, `run_summary`, `ensemble_id?`, `path_index?` | `ensemble_id` is set when the run is one path of an ensemble |
| `agents` | `id`, `simulation_id →`, `name`, `agent_type`, `config`, `latency_config`, `final_metrics`, `risk_flags` | Resolved config is stored, not the request |
| `simulation_steps` | `simulation_id →`, `step`, `reference_price`, `mid_price?`, `mark_price?`, `best_bid?`, `best_ask?`, `spread?`, `volatility`, `trade_count`, `shock_fired` | `mark_price` is the price actually used for unrealized PnL |
| `orders` | `simulation_id →`, `agent_id →`, `engine_order_id`, `sequence`, `step`, `side`, `order_type`, `price?`, `quantity`, `filled_quantity`, `status`, `cancelled_at_step?`, `cancelled_at_sequence?` | The replay stream. `sequence` is the engine's counter, not a clock |
| `trades` | `simulation_id →`, `step`, buy/sell order ids, buy/sell `agent_id →`, `aggressor_side`, `price`, `quantity`, `mid_at_trade?`, `effective_half_spread?`, `realised_half_spread?`, `price_impact?`, `horizon_steps?` | Decomposition stored per trade with the horizon it was measured at |
| `agent_performance` | `agent_id →`, `simulation_id →`, `step`, `inventory`, `realized_pnl`, `unrealized_pnl`, `total_pnl`, `mark_price?`, `inventory_risk_score` | One row per agent per persisted step |
| `ensembles` / `sweeps` | `id`, `name`, `status`, `shared_config`, `engine_config`, `aggregates` / `grid_spec`, `pbo`, `best_cell`, `robust_centroid` | Background job state and results |

**Relationships.** `simulations 1─N agents`, `1─N simulation_steps`, `1─N orders`, `1─N trades`;
`agents 1─N orders`, `1─N agent_performance`. `ensembles 1─N simulations` via `ensemble_id`.
Every foreign key is `ON DELETE CASCADE` — deleting a simulation removes its entire footprint.

**Indexes.** `ix_simulation_steps_sim_step`, `ix_orders_sim_step`, `ix_orders_agent`,
`ix_trades_sim_step`, `ix_agent_performance_agent_step`. Each exists because a read path scans
that composite: the dashboard fetches by `(simulation, step)` and replay scans a simulation's
order stream in sequence order.

**Constraints.** `nullable=False` carries meaning here. A nullable `mid_price` means *the book
had no two sides*, which is a real state the dashboard counts and reports; a nullable
`effective_half_spread` means *not measurable at this horizon*. These are not missing data.

**Migrations.** Alembic, applied on application startup. Two revisions: a baseline reproducing
the pre-Alembic schema exactly, and the latency/microstructure/ensembles columns.
`render_as_batch=True` so SQLite can rebuild tables for constraint changes. A database created
before Alembic is stamped with the baseline rather than re-created. A test asserts the migrated
schema and the models agree.

## 2.3 APIs

No authentication on any route (see §2.4). All bodies are JSON. Validation errors are FastAPI's
standard `422` shape, naming the offending field.

### Simulations

| Method | Route | Body / params | Returns | Errors |
|---|---|---|---|---|
| `POST` | `/simulations` | name, `price_process`, `shocks[]` | `201` SimulationRead | `422` duplicate shock steps, bad σ |
| `GET` | `/simulations` | — | list | — |
| `GET` | `/simulations/{id}` | — | SimulationRead + agents | `404` |
| `DELETE` | `/simulations/{id}` | — | `204` | `404` |
| `POST` | `/simulations/{id}/agents` | type, config, latency | `201` AgentRead | `404`, `422` unknown config or latency key, `409` at the 12-agent cap |
| `GET` | `/simulations/{id}/agents` | — | list | `404` |
| `POST` | `/simulations/{id}/run` | `steps` ≤ 20,000, `persist_every_n_steps` | RunSummary | `404`, `422` shock scheduled past the end of the run |
| `GET` | `/simulations/{id}/order-book/snapshot` | `?step=` | OrderBookSnapshot | `404`, `422` step out of range |
| `GET` | `/simulations/{id}/agents/{aid}/performance` | — | per-step series | `404` agent in another simulation |
| `GET` | `/simulations/{id}/comparison` | — | metrics, market series, conservation residual | `404`, `409` run not complete |
| `GET` | `/simulations/{id}/liquidity-surface` | — | packed grid | `404`, `409` |
| `GET` | `/simulations/{id}/microstructure` | — | effective / realised / impact by horizon and agent | `404`, `409` run predates the decomposition — re-run it |
| `GET` | `/simulations/{id}/latency-races` | — | contested fills + profiles | `404`, `409` |
| `GET` | `/simulations/{id}/microprice` | `?stride=` | microprice vs mid + forecast RMSE | `404`, `409` |
| `GET` | `/simulations/{id}/greeks/attribution` | — | gamma/theta/vega/slippage per step | `404`, `409` no options maker in this run |

### Ensembles and sweeps

| Method | Route | Returns | Notes |
|---|---|---|---|
| `POST` | `/ensembles` | `202` | Runs on a FastAPI background task over a process pool |
| `GET` | `/ensembles/{id}` | status, `completed_paths`, aggregates | Poll for progress |
| `GET` | `/ensembles/{id}/distribution` | histograms, bootstrap CIs, max conservation residual across paths | `409` before completion |
| `GET` | `/ensembles/{id}/paths` | per-path rows | |
| `POST` | `/sweeps` | `202` | `422` if a swept parameter is misspelled — before the grid runs |
| `GET` | `/sweeps/{id}` | cells, PBO, best cell, robust centroid | |
| `GET` | `/sweeps/{id}/surface` | 2D slice | `422` unknown metric or unswept parameter |

### Meta

`GET /meta/agent-defaults` — every tunable with its default.
`GET /meta/engine-config` — every formula and constant in force. This exists so a result can be
audited without reading the source, and so a stored run can be reproduced years later.

## 2.4 Security

**Stated plainly: there is no authentication or authorization, and the system must not be
exposed to the internet.** It is a single-operator research tool that runs on localhost or
behind a private network. Adding auth would be ceremony around a tool with no multi-user
concept and no data worth stealing. This is a deliberate scope decision, recorded here so it
is not mistaken for an oversight.

What *is* enforced:

| Control | Implementation |
|---|---|
| Input validation | Pydantic on every route; bounded fields (`steps ≤ 20_000`, `agents ≤ 12`, `shocks ≤ 20`, `0 < σ ≤ 10`) |
| Strict config | Unknown agent, latency or sweep keys are rejected with `422` rather than defaulted — a typo must not silently change a result |
| SQL injection | No raw SQL on any request path; SQLAlchemy throughout |
| CORS | Explicit allow-list from `CORS_ORIGINS`, defaulting to localhost dev ports |
| Secrets | None in the repository. The only credential is `DATABASE_URL`, supplied by environment |
| Resource bounds | Step cap, agent cap, grid-size cap, and latency races capped at 200 records to bound memory |
| Data privacy | No personal data is collected, stored or transmitted. No telemetry, no external calls |

**Known exposure if deployed publicly:** no rate limiting, and a 20,000-step run holds a
request open — trivially abusable as a denial of service. Addressing that means a job queue and
a reverse proxy, which is the same work listed under *Next*.

## 2.5 Deployment

**Hosting.** `docker compose up --build` brings up four services: `db` (postgres:16-alpine),
`backend` (uvicorn), `frontend` (nginx serving the Vite build and proxying `/api`), and `tests`
(a one-shot runner). Nothing requires a cloud account.

**Environment variables.**

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://…` | SQLite URL runs the whole system with no database server |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Comma-separated allow-list |
| `VITE_PROXY_TARGET` | `http://localhost:8000` | Dev-server proxy target |

Every engine constant in `config.py` is also settable by environment, which is how a run is
reconfigured without touching code.

**Build.** Backend: `pip install -r requirements-dev.txt`, migrations run at startup. Frontend:
`npm ci && npm run build` — `ci`, not `install`, so the lockfile is authoritative.

**CI.** Two jobs on every push and pull request: backend tests on Python 3.12, frontend
typecheck and build on Node 22. The backend job also runs a 400-step smoke simulation and fails
if it produces zero trades — the class of bug no unit test catches, where every agent is
individually correct and the market is dead.

**Monitoring and logs.** Standard uvicorn access logs plus a named `tradearenax.events` logger
for engine diagnostics. There is no metrics backend, no tracing and no alerting; for a tool
that runs on request and whose correctness is asserted in-process by the conservation invariant,
the invariant *is* the monitoring.

---

# Part 3 — Roadmap and risk

## 3.1 Next increments

**One week — close the UI gap.** Ensembles and sweeps are fully implemented and tested
server-side but have no interface, so the statistics that most distinguish the project are
reachable only through the API. Build the two pages: a distribution view with bootstrap CIs,
and a sweep surface with the PBO and robust centroid called out.

**Two weeks — make it live.** Replace dashboard polling with a WebSocket feed, and move
ensembles and sweeps onto a real job queue (Redis or ARQ) so they survive a restart and can run
on more than one process. These are the two limitations that currently bound the system's size.

## 3.2 Risk areas

| Risk | Severity | Position |
|---|---|---|
| Synthetic prices limit external validity | High, inherent | Stated prominently rather than hedged; the mechanics are the contribution, not the price path |
| Maker sees true fair value | Medium | A real informational edge over the directional agents. Those provably do not see it — a test perturbs the field 50% and asserts their decisions are unchanged |
| Latency degrades the book | Medium | A slow maker produces more one-sided steps. Real behaviour, counted and surfaced, not suppressed |
| Synchronous runs hold a request | Medium | Bounded by the step cap today; needs a job queue past that |
| Float accumulation in book arithmetic | Low, now handled | Caused a real phantom-level bug; `_QTY_EPSILON` plus a regression test reproducing the exact nine-slice case |
| Transitive dependency drift | Low, now handled | An unpinned `anyio` broke CI while local venvs passed. Warning filtered by message rather than pinning a transitive dependency |

## 3.3 Testing plan

The suite is organised by **the property being defended**, not by source file — see the table
in [`architecture.md`](architecture.md). Four tiers:

1. **Invariants, on every run, not only in tests** — PnL conservation, uncrossed book, cached
   level quantity equals live queue quantity, unique sequence numbers.
2. **Equivalence** — the zero-latency event engine against the original step loop over 300
   steps, with and without a shock. Without this, "more realistic" is indistinguishable from
   "changed".
3. **Properties over examples** — realised σ recovers configured σ over 20,000 steps; bootstrap
   CIs cover at the nominal rate; PBO ≈ 0.5 for pure noise; Greeks against finite differences.
4. **Cross-process reproducibility** — the same simulation run in subprocesses under three
   `PYTHONHASHSEED` values must match. This caught a silent violation of the project's central
   promise.

## 3.4 Review checklist

- [x] `docker compose up --build` works from a clean clone
- [x] Test suite green: 540 passed, 13 skipped
- [x] Typecheck and production build clean
- [x] Migrations apply to an empty database and to a pre-Alembic one
- [x] Conservation invariant holds on every run and is surfaced in the UI
- [x] Every engine constant readable at `/meta/engine-config`
- [x] Every panel's rows exportable as CSV
- [x] Measured results that contradict the design are reported, not hidden
- [ ] Ensembles and sweeps reachable from the UI
- [ ] Streaming instead of polling
