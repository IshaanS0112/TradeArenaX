# Running TradeArena X — step by step

Written for macOS (your machine). Every command below was verified end to end.

There are two ways to run this. **Pick one:**

- **Path A — Docker.** One command, everything works, nothing to install. Use this
  for demos and screen recordings.
- **Path B — manual.** Two terminals, more setup, but you get hot reload and can
  actually debug. Use this while developing.

If you just want to see it work right now, use Path A.

---

## Step 0 — Open the right folder (do this first, both paths)

Open **Terminal** (Cmd+Space, type "Terminal", Enter) and run:

```bash
cd ~/BASE/02-projects/TradeArenaX
```

Confirm you are in the right place:

```bash
ls
```

You should see exactly this:

```
LICENSE   PUSH_TO_GITHUB.sh  README.md  RUNNING.md
backend   docker-compose.yml  docs       frontend
```

If you see `AstraSynth`, `FinSight360`, … you are one level too high — run
`cd TradeArenaX`.

**Everything below assumes you are in this folder.** When a step says "new
terminal", open one (Cmd+T) and `cd ~/BASE/02-projects/TradeArenaX` again.

---

# Path A — Docker (recommended first run)

## A1. Check Docker is installed and running

```bash
docker --version
```

- **Prints a version** → good, continue.
- **`command not found`** → install Docker Desktop from
  https://www.docker.com/products/docker-desktop, open it, wait for the whale
  icon in your menu bar to stop animating, then retry.

Docker Desktop must be **open and running**, not just installed. If the whale icon
isn't in your menu bar, open the Docker app from Applications.

## A2. Start everything

```bash
docker compose up --build
```

First run takes **3–6 minutes** (it downloads Postgres, Python and Node images and
builds two containers). Later runs take ~10 seconds.

You'll see a lot of log output. Wait until you see lines like:

```
tradearenax-db-1        | database system is ready to accept connections
tradearenax-backend-1   | INFO:     Application startup complete.
tradearenax-backend-1   | TradeArena X up. tick=0.01 capital_base=100000.0 ...
tradearenax-frontend-1  | ... nginx ... start worker processes
```

**Leave this terminal running.** Closing it stops the app.

## A3. Open it

In your browser:

| What | URL |
|---|---|
| **The dashboard** | http://localhost:5173 |
| API docs (Swagger) | http://localhost:8000/docs |
| Every formula in force | http://localhost:8000/meta/engine-config |

## A4. Stopping it

Press **Ctrl+C** in that terminal. To also delete the database:

```bash
docker compose down -v
```

Skip to **"Step 3 — Check it's actually working"** below.

---

# Path B — Manual (for development)

You need **two terminals** running at the same time.

## B1. Check your Python version

```bash
python3 --version
```

**You need 3.10 or higher.** macOS ships 3.9, which will not work — the code uses
`X | None` type syntax that 3.9 evaluates as an error at import time.

If you're on 3.9 or lower:

```bash
brew install python@3.12
```

Then use `python3.12` instead of `python3` in the next step.

## B2. Terminal 1 — the backend

```bash
cd ~/BASE/02-projects/TradeArenaX/backend

# Create an isolated environment so these packages don't pollute your system
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should now start with `(.venv)`. That means it worked.

```bash
pip install -r requirements-dev.txt
```

Takes 1–2 minutes. `psycopg2-binary` installs from a prebuilt wheel, so you do
**not** need Postgres installed for this step.

Now start the server:

```bash
export DATABASE_URL="sqlite:///./tradearenax.db"
uvicorn app.main:app --reload
```

**That `export` line is not optional.** The default `DATABASE_URL` points at
Postgres, so without it the server tries to connect to a database you don't have
and dies with a wall of `OperationalError: connection refused`. The SQLite URL
makes it use a local file instead — no database server needed.

You should see:

```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
TradeArena X up. tick=0.01 capital_base=100000.0 steps_per_year=98280 ...
```

**Leave this terminal running.**

## B3. Terminal 2 — the frontend

Open a new terminal (**Cmd+T**):

```bash
cd ~/BASE/02-projects/TradeArenaX/frontend
npm install     # 1-2 minutes, only needed the first time
npm run dev
```

You should see:

```
VITE v6.x.x  ready in 400 ms
➜  Local:   http://localhost:5173/
```

Open **http://localhost:5173**.

> The frontend calls `/api/*` on its own origin and Vite proxies that to
> `localhost:8000`. That's why the backend must be running first — and why you
> never have to deal with CORS.

## B4. Stopping it

**Ctrl+C** in each terminal. Next time you only need:

```bash
# Terminal 1
cd ~/BASE/02-projects/TradeArenaX/backend
source .venv/bin/activate
export DATABASE_URL="sqlite:///./tradearenax.db"
uvicorn app.main:app --reload

# Terminal 2
cd ~/BASE/02-projects/TradeArenaX/frontend && npm run dev
```

---

# Step 3 — Check it's actually working

Do these four in order. They go from "did it start" to "are the numbers right".

## Check 1 — Is the backend alive? (5 seconds)

New terminal:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok"}`

If you get `Connection refused`, the backend isn't running — go back to A2 or B2.

## Check 2 — Run the test suite (30 seconds)

This is the real proof the engine is correct.

```bash
cd ~/BASE/02-projects/TradeArenaX/backend
source .venv/bin/activate          # skip this line if you used Docker
python -m pytest
```

**Expected last line:**

```
210 passed in 5.02s
```

If you used Docker and don't have a venv:

```bash
cd ~/BASE/02-projects/TradeArenaX
docker compose run --rm --build tests
```

> **Not** `docker compose exec backend python -m pytest`. The running `backend`
> container is the production image: it installs `requirements.txt` only and does
> not copy `tests/`, so it has neither pytest nor the suite. That is deliberate —
> test tooling does not belong in a deployed image. The `tests` service above
> builds the Dockerfile's separate `test` stage, which layers pytest and the suite
> on top of the identical production image.

Anything other than "210 passed" means something is wrong — send me the output.

## Check 3 — Run a simulation from the command line (10 seconds)

This runs the whole engine with no database and no browser, and prints the result.
It's the fastest way to confirm the economics work.

```bash
cd ~/BASE/02-projects/TradeArenaX/backend
source .venv/bin/activate
python scripts/demo_run.py --steps 1000 --shock-step 500 --shock-pct -8
```

**Expected output** (these exact numbers, because the run is seeded):

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

**The line that matters is the last one.** Every trade moves money between two
agents and nothing enters or leaves, so the total must be zero. If that number is
not ~0 (like `1e-12` or smaller), the accounting is broken.

If you get **`Trades: 0`**, something is wrong — the agents never interacted.

## Check 4 — Use the dashboard (2 minutes)

Go to **http://localhost:5173** and do this:

1. Click **"New run"** in the top nav.
2. Leave every default as is (1000 steps, seed 42, shock at step 500).
3. Click **"Create and run"**. It takes 2–5 seconds.

You land on the results page. Now verify each tab:

**Comparison tab** — you should see:
- A price chart with a red dashed **"shock"** line at step 500, and a visible drop.
- A table of 3 agents ranked by PnL.
- Below the table: *"PnL conservation check: 1.42e-12 — agents' PnL sums to minus
  the fees collected, as a closed market must."* **If this line is red, something
  is wrong.**

**Agents tab** — you should see:
- A PnL chart with 3 lines that visibly kink at the shock.
- An inventory chart. Compare the two: wherever an agent's PnL drops hardest, its
  inventory ran one-directional. That's the whole story of market making.

**Order book tab** — this is the best part to demo:
- A bid/ask ladder with green and red depth bars.
- A **slider** at the top right. Drag it to step 498, note the prices (~99.4 /
  100.0). Drag to step 502 — the whole book has jumped down to ~91.3 / 91.9.

  You're watching the book get repriced through the crash. This works because the
  book is *reconstructed from the stored order log*, not cached.

---

# If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `OperationalError` / `connection refused` on backend start | `DATABASE_URL` not set, defaults to Postgres | `export DATABASE_URL="sqlite:///./tradearenax.db"` before `uvicorn` |
| `command not found: python3` | Python not installed | `brew install python@3.12` |
| `SyntaxError` / `TypeError` on import | Python 3.9 or older | `brew install python@3.12`, recreate the venv with `python3.12 -m venv .venv` |
| `command not found: npm` | Node not installed | `brew install node` |
| `Address already in use` port 8000 | Old server still running | `lsof -ti:8000 \| xargs kill -9` |
| `Address already in use` port 5173 | Old Vite still running | `lsof -ti:5173 \| xargs kill -9` |
| Dashboard loads but every panel is empty | Backend not running | Check `curl localhost:8000/health` |
| `Cannot connect to the Docker daemon` | Docker Desktop not open | Open Docker Desktop, wait for the whale to settle |
| `pip: command not found` after `source .venv/bin/activate` | venv wasn't created | Delete `.venv`, redo B2 |
| Frontend shows a red error bar | The API returned an error — it's printed verbatim | Read the message; it names the exact bad parameter |

**Nuclear reset** (Docker path):

```bash
cd ~/BASE/02-projects/TradeArenaX
docker compose down -v
docker compose up --build
```

**Nuclear reset** (manual path):

```bash
cd ~/BASE/02-projects/TradeArenaX/backend
rm -rf .venv tradearenax.db
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt
```

---

# Demo script (for an interview)

Five minutes, in this order:

1. **"This is a limit order book, not a price simulator."** Order book tab. Point at
   the ladder: real bid/ask levels, real depth, real order counts.
2. **Drag the slider through step 500.** "The book reprices through an 8% crash.
   This is reconstructed from the persisted order log — every book state is
   reproducible."
3. **Agents tab.** "Three strategies traded against each other. Watch where the
   market maker's PnL breaks — and look at its inventory at the same moment. It
   got picked off holding a long position into the crash. That's adverse
   selection."
4. **Comparison tab, point at the conservation line.** "This is how I know the
   accounting is right. Money moves between agents and nothing leaves, so the sum
   is zero. It runs on every simulation, not just in tests."
5. **`python -m pytest` in a terminal.** "210 tests. Including one that perturbs the
   latent price by 50% and asserts the directional agents don't change a single
   decision — that's a look-ahead bias check."

If they ask what's real: **the matching engine, the strategies, the PnL maths and
the risk metrics are real. The price path is synthetic geometric Brownian motion.**
Say it plainly — it's the standard setup for strategy research, and it's in the
README.
