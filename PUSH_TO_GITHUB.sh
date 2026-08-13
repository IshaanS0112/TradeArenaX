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
if [ ! -d backend/.venv ]; then
  # Build a venv rather than reaching for Docker. Needing a container daemon
  # running in order to execute a pure Python test suite is a dependency this
  # script should not have.
  #
  # Version matters: this stack is pinned, and pinned packages only have wheels
  # for the interpreters that existed when they were released. On 3.13+ pip falls
  # back to building from source, and psycopg2-binary then demands PostgreSQL's
  # pg_config. 3.12 is what the Dockerfile and CI use, so it is what a local venv
  # should use too.
  PY=""
  for candidate in python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1; then PY=$(command -v "$candidate"); break; fi
  done

  if [ -z "$PY" ]; then
    # Fall back to whatever python3 is, but only if it is in the supported range.
    FALLBACK=$(command -v python3 || true)
    if [ -n "$FALLBACK" ] && "$FALLBACK" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info < (3,13) else 1)'; then
      PY="$FALLBACK"
    else
      FOUND=$("${FALLBACK:-python3}" --version 2>&1 || echo "none")
      echo "ERROR: need Python 3.10-3.12, found $FOUND." >&2
      echo "       This project pins its dependencies and 3.13+ has no wheels for them." >&2
      echo "       Fix:  brew install python@3.12  then re-run this script." >&2
      exit 1
    fi
  fi

  echo "    creating backend/.venv with $("$PY" --version) (about 60 seconds, one time only)"
  "$PY" -m venv backend/.venv
  ./backend/.venv/bin/pip install --quiet --upgrade pip
  # requirements-test.txt, not requirements-dev.txt: the suite runs on SQLite and
  # does not need a PostgreSQL driver. CI still installs the full production set.
  ./backend/.venv/bin/pip install --quiet -r backend/requirements-test.txt
fi
(cd backend && ./.venv/bin/python -m pytest -q)

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
