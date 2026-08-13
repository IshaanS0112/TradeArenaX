# Pushing TradeArena X to GitHub — step by step

Your GitHub account: **`Ishaana0112`** → https://github.com/Ishaana0112

I ran a dry-run commit of this project. **77 files, 628 KB.** Nothing bad leaks in —
no `node_modules` (82 MB on your disk), no `.venv`, no `.env`, no `.pyc`, no
`.db`. The `.gitignore` is doing its job, so you can commit with confidence.

---

## Step 0 — One-time setup (skip if you've pushed before)

### 0a. Tell git who you are

```bash
git config --global user.name "Ishaan Singh"
git config --global user.email "ishu2004.ind@gmail.com"
```

Without this, `git commit` fails with `Please tell me who you are`. Check it worked:

```bash
git config --global user.name && git config --global user.email
```

> Use the same email as your GitHub account, or your commits won't link to your
> profile and the contribution graph stays empty — which defeats half the point of
> pushing.

### 0b. Install and log in to the GitHub CLI

```bash
brew install gh
gh auth login
```

Answer the prompts: **GitHub.com** → **HTTPS** → **Yes** (authenticate git) →
**Login with a web browser** → copy the code → press Enter → paste in browser →
Authorize.

Verify:

```bash
gh auth status
```

Should say `Logged in to github.com account Ishaana0112`.

> **Why `gh` and not just `git`?** Since 2021, GitHub rejects your account password
> over HTTPS. Without `gh` you'd have to create a Personal Access Token by hand and
> paste it as your password every time. `gh auth login` handles all of that once.
> If you'd rather not install it, see *Alternative* at the bottom.

---

## Step 1 — Go to the project

```bash
cd ~/BASE/02-projects/TradeArenaX
```

Confirm you're in the right place — you should see `docker-compose.yml`:

```bash
ls docker-compose.yml
```

---

## Step 2 — Make sure it actually works before you push

Never push something you haven't run. This takes 40 seconds.

```bash
cd backend
source .venv/bin/activate     # if you don't have .venv yet, see RUNNING.md step B2
python -m pytest
```

**Expected: `210 passed`.** Then:

```bash
cd ../frontend
npm run typecheck && npm run build
cd ..
```

**Expected: no errors, and `✓ built in ...`.**

If either fails, stop and fix it. A repo whose CI badge is red on the first commit
is worse than no repo.

---

## Step 3 — Push it

### The easy way (one command)

I wrote a script that does everything: runs the tests, runs the frontend build,
initialises git, commits, creates the GitHub repo, and pushes.

```bash
cd ~/BASE/02-projects/TradeArenaX
source backend/.venv/bin/activate     # the script runs pytest, so it needs this
./PUSH_TO_GITHUB.sh
```

It prints your repo URL when it finishes. **Skip to Step 4.**

> If you get `permission denied`, run `chmod +x PUSH_TO_GITHUB.sh` first.
> The script stops on the first failure, so it will never push a broken build.

### The manual way (so you understand what the script did)

```bash
cd ~/BASE/02-projects/TradeArenaX

# 1. Start a git repository on the 'main' branch
git init -b main

# 2. Stage everything (.gitignore filters out node_modules, .venv, .env, etc.)
git add .

# 3. LOOK at what you're about to commit - always do this
git status --short
```

You should see ~77 lines, all starting with `A`. **If you see `node_modules` or
`.venv` anywhere, stop** — run `git rm -r --cached .` and check `.gitignore`.

Sanity-check the size:

```bash
git diff --cached --name-only | xargs du -ch | tail -1
```

Should be well under 1 MB. If it's 80 MB+, `node_modules` got in.

```bash
# 4. Commit
git commit -m "TradeArena X: price-time-priority limit order book, inventory-aware market making, momentum and mean-reversion agents, volatility shock stress testing"

# 5. Create the repo on GitHub and push in one go
gh repo create tradearenax --public --source=. --remote=origin --push \
  --description "Market-making and trading strategy simulation: price-time-priority limit order book, three agent archetypes, volatility shock stress testing, Sharpe/drawdown comparison (FastAPI, React, PostgreSQL, Docker). Synthetic price data; not a live trading system."
```

Done. Your repo: **https://github.com/Ishaana0112/tradearenax**

---

## Step 4 — Verify the push actually worked

Don't assume. Check these four:

### 4a. The files are there

```bash
gh repo view --web
```

Opens the repo in your browser. Confirm:
- The README renders with the tables and headings formatted (not raw markdown).
- You see `backend/`, `frontend/`, `docs/`, `.github/`.
- You do **not** see `node_modules` or `.venv`.

### 4b. CI is running

Click the **Actions** tab. A workflow called **CI** should be running or done.

Wait ~2 minutes. You want **two green checkmarks**: `Backend tests` and
`Frontend typecheck and build`.

If it's red, click into it and read the failing step. Most likely cause on a first
push is a dependency that installs locally but not on a clean Ubuntu runner.

### 4c. The repo size is sane

On the repo page, the language bar should show Python/TypeScript. If GitHub says
the repo is hundreds of MB, something leaked.

### 4d. It clones and runs from scratch

The real test — this is what a recruiter or interviewer does:

```bash
cd /tmp
git clone https://github.com/Ishaana0112/tradearenax.git
cd tradearenax
docker compose up --build
```

If that opens a working dashboard at http://localhost:5173, you're genuinely done.

---

## Step 5 — Make the repo look professional (5 minutes, high value)

This is the part most students skip, and it's what a recruiter actually sees.

### 5a. Add topics

On the repo page, click the **⚙️ gear** next to "About" (top right), then in
**Topics** add:

```
quantitative-finance  market-microstructure  order-book  market-making
algorithmic-trading   fastapi  react  typescript  postgresql  docker  python
```

Topics are how people find your repo and how a recruiter instantly classifies it.

### 5b. Check the About description

Same panel. It should already be set from the `--description` flag. Confirm it
mentions **"Synthetic price data; not a live trading system"** — leading with that
reads as rigour, not as a weakness.

### 5c. Add the CI badge to your README

At the very top of `README.md`, right under `# TradeArena X`, add:

```markdown
[![CI](https://github.com/Ishaana0112/tradearenax/actions/workflows/ci.yml/badge.svg)](https://github.com/Ishaana0112/tradearenax/actions/workflows/ci.yml)
```

Then:

```bash
git add README.md
git commit -m "docs: add CI badge"
git push
```

A green badge at the top of a README is the fastest possible signal that the code
runs.

---

## Making changes after the first push

```bash
cd ~/BASE/02-projects/TradeArenaX

# always run the tests first
cd backend && source .venv/bin/activate && python -m pytest && cd ..

git add .
git status --short          # look before you leap
git commit -m "clear description of what changed"
git push
```

**Write real commit messages.** `git log` is public, and `fix`, `update`, `final`
tell a reviewer nothing. Compare:

- Bad: `fixed bug`
- Good: `fix: interleave cancels by sequence in book replay, not by step`

Your HPC-Vision history is `Initial commit` → `final project` → `final project`.
Don't repeat that here — a clean commit log is genuinely part of what a strong repo
looks like.

---

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Please tell me who you are` | git identity not set | Step 0a |
| `gh: command not found` | GitHub CLI not installed | `brew install gh` |
| `authentication failed` on push | GitHub rejects passwords over HTTPS | `gh auth login` |
| `remote origin already exists` | You ran the script twice | `git remote remove origin`, then retry |
| `repository already exists` | Name is taken on your account | Use a different name, or `gh repo delete tradearenax` |
| `failed to push some refs` | Remote has commits yours doesn't | `git pull --rebase origin main` then push |
| `src refspec main does not match any` | Nothing committed yet | Run `git commit` first |
| Push is huge / slow | `node_modules` got staged | `git rm -r --cached frontend/node_modules`, commit, retry |
| `error: failed to push` + file over 100 MB | A large file is in history | Nothing in this project is over 96 KB — if you hit this, you added something new |

**Start completely over** (safe — you lose only local git history, not your code):

```bash
cd ~/BASE/02-projects/TradeArenaX
rm -rf .git
# then redo Step 3
```

---

## Alternative: no `gh` CLI

If you'd rather not install the CLI:

1. Go to https://github.com/new
2. **Repository name:** `tradearenax`
3. **Public**
4. **Do not** tick "Add a README", "Add .gitignore", or "Choose a license" — you
   already have all three, and ticking them creates a conflicting first commit.
5. Click **Create repository**, then:

```bash
cd ~/BASE/02-projects/TradeArenaX
git init -b main
git add .
git commit -m "TradeArena X: limit order book simulation with market-making, momentum and mean-reversion agents"
git remote add origin https://github.com/Ishaana0112/tradearenax.git
git push -u origin main
```

When it asks for a password, **your GitHub password will not work.** Create a token
at https://github.com/settings/tokens → *Generate new token (classic)* → tick the
**`repo`** scope → generate → copy it → paste it as the password. Save it somewhere;
GitHub shows it only once.

---

## While you're at it: HPC-Vision

That repo currently **cannot be pushed** — four 225 MB files are in its committed
history and GitHub's per-file limit is 100 MB (hard limit, not a warning). The fix
is in `02-projects/PROJECT_AUDIT_2026-07-30.md`. It's a 10-minute job and it
unblocks a repo that's currently invisible.
