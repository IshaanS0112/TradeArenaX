# Multi-stage on purpose. The runtime image ships the application and its
# production dependencies and nothing else - no pytest, no httpx, no test suite.
# A separate `test` stage layers those on top so the suite can be run in an
# environment identical to production without putting test tooling into the
# image that gets deployed.
#
#   docker compose up                          -> builds `runtime` (the last stage)
#   docker compose run --rm --build tests      -> builds and runs `test`

FROM python:3.12-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home --uid 1000 trade && chown -R trade:trade /app


# --- Test stage -------------------------------------------------------------
# Not part of the deployed image. Everything below this line exists so that
# `pytest` runs against the same Python version and the same pinned production
# dependencies that production uses.
FROM base AS test

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY pytest.ini .
COPY tests ./tests

RUN chown -R trade:trade /app
USER trade

# conftest.py points DATABASE_URL at a temp-directory SQLite file, so the suite
# needs no database container and no environment configuration.
CMD ["python", "-m", "pytest", "-q"]


# --- Runtime stage ----------------------------------------------------------
# Declared last so a plain `docker build` / `docker compose build` produces it
# by default.
FROM base AS runtime

USER trade

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD curl -fsS http://localhost:8000/health || exit 1

# One worker by default. The simulation loop is CPU-bound and holds no shared
# state between requests, so scaling out means more workers - but every worker
# needs its own database connection pool, so raise both together or neither.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
