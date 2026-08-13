#!/usr/bin/env bash
# Push TradeArena X to https://github.com/IshaanS0112/TradeArenaX
#
# The repo already exists and is empty, so this attaches a remote and pushes
# rather than creating anything. Safe to re-run: it skips any step already done.
#
#   cd ~/BASE/02-projects/TradeArenaX && ./PUSH_TO_GITHUB.sh
set -euo pipefail

REMOTE="https://github.com/IshaanS0112/TradeArenaX.git"

# --- Verify before publishing ------------------------------------------------
# Nothing gets pushed unless the suite and the frontend build both pass. A repo
# whose CI is red on its first commit is worse than no repo.
echo "==> Backend tests"
if [ -d backend/.venv ]; then
  (cd backend && ./.venv/bin/python -m pytest -q)
else
  echo "    no backend/.venv found, running the suite in Docker instead"
  docker compose run --rm --build tests
fi

echo "==> Frontend typecheck and build"
(cd frontend && npm install --silent && npm run typecheck && npm run build)

# --- Git ---------------------------------------------------------------------
if [ ! -d .git ]; then
  echo "==> git init"
  git init -b main
fi

echo "==> Staging"
git add .

echo "==> About to commit these files:"
git diff --cached --name-only | wc -l | xargs echo "    file count:"
git diff --cached --name-only | xargs du -ch 2>/dev/null | tail -1 | xargs echo "    total size:"

# Fail loudly rather than pushing 80 MB of dependencies.
if git diff --cached --name-only | grep -qE 'node_modules|\.venv/|__pycache__|\.env$'; then
  echo "ERROR: build artefacts or secrets are staged. Check .gitignore." >&2
  git diff --cached --name-only | grep -E 'node_modules|\.venv/|__pycache__|\.env$' >&2
  exit 1
fi

if git diff --cached --quiet; then
  echo "==> Nothing to commit"
else
  git commit -m "TradeArena X: price-time-priority limit order book, inventory-aware market making, momentum and mean-reversion agents, volatility shock stress testing"
fi

if ! git remote get-url origin >/dev/null 2>&1; then
  echo "==> Adding remote"
  git remote add origin "$REMOTE"
fi

echo "==> Pushing"
git push -u origin main

echo
echo "Done -> https://github.com/IshaanS0112/TradeArenaX"
echo "Now set the description and topics:  see PUSHING.md step 5"
