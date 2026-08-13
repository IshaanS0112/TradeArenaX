#!/usr/bin/env python3
"""Run one simulation end to end and print the comparison table.

No database, no HTTP: this drives the engine directly, which is the fastest way
to check that a change to the matching engine or the agents did not quietly
break the economics.

    cd backend && python scripts/demo_run.py --steps 800 --shock-step 400
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.enums import AgentType  # noqa: E402
from app.services.price_process import PriceProcess, VolatilityShock  # noqa: E402
from app.services.simulation_engine import SimulationEngine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=800)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--volatility", type=float, default=0.30)
    parser.add_argument("--drift", type=float, default=0.0)
    parser.add_argument("--shock-step", type=int, default=400)
    parser.add_argument("--shock-pct", type=float, default=-8.0)
    args = parser.parse_args()

    settings = get_settings()
    shocks = ()
    if args.shock_step and args.shock_step <= args.steps:
        shocks = (
            VolatilityShock(
                step=args.shock_step,
                magnitude_pct=args.shock_pct,
                vol_multiplier=3.0,
                vol_half_life_steps=60,
            ),
        )

    process = PriceProcess(
        initial_price=100.0,
        drift=args.drift,
        volatility=args.volatility,
        dt=settings.dt,
        seed=args.seed,
        shocks=shocks,
    )
    engine = SimulationEngine(price_process=process, settings=settings)
    engine.add_agent("mm", AgentType.MARKET_MAKER, {})
    engine.add_agent("mom", AgentType.MOMENTUM, {})
    engine.add_agent("rev", AgentType.MEAN_REVERSION, {})

    result = engine.run(args.steps)

    print(f"\nSteps: {result.steps_run}   Trades: {len(result.fills)}   "
          f"Orders: {len(result.orders)}")
    print(f"Reference price: {result.reference_path[0]:.2f} -> "
          f"{result.reference_path[-1]:.2f}")
    print(f"Volatility  configured {result.configured_volatility:.3f}  "
          f"realized {result.realized_volatility:.3f}"
          if result.realized_volatility
          else "")
    print(f"Steps without a two-sided book: {result.steps_with_no_two_sided_book}")

    header = (
        f"\n{'agent':<6}{'total pnl':>12}{'realized':>12}{'unreal':>10}"
        f"{'sharpe':>10}{'maxDD%':>9}{'win%':>8}{'trips':>7}{'fills':>7}{'inv':>8}"
    )
    print(header)
    print("-" * len(header.strip()))
    for agent_id, s in sorted(
        result.summaries.items(), key=lambda kv: kv[1].total_pnl, reverse=True
    ):
        print(
            f"{agent_id:<6}{s.total_pnl:>12.2f}{s.realized_pnl:>12.2f}"
            f"{s.unrealized_pnl:>10.2f}"
            f"{_f(s.sharpe_ratio, 2):>10}{_f(s.max_drawdown_pct, 2):>9}"
            f"{_f(None if s.win_rate is None else s.win_rate * 100, 1):>8}"
            f"{s.closed_round_trips:>7}{s.fill_count:>7}{s.final_inventory:>8.0f}"
        )

    total = sum(s.total_pnl for s in result.summaries.values())
    fees = sum(s.fees_paid for s in result.summaries.values())
    print(
        f"\nPnL conservation check: sum(total_pnl) + fees = {total + fees:+.10f} "
        "(must be ~0: nothing enters or leaves a closed market except fees)"
    )
    for w in result.warnings:
        print(f"  warning: {w}")
    for agent_id, state in result.agent_states.items():
        for flag in state.agent.flags[:5]:
            print(f"  [{agent_id}] {flag}")
    return 0


def _f(value: float | None, digits: int) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


if __name__ == "__main__":
    raise SystemExit(main())
