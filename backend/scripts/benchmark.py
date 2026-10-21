#!/usr/bin/env python3
"""Regenerate the numbers the README quotes.

A results table is only worth printing if a reader can reproduce it. This drives
the SDK on fixed seeds and prints the same figures the README's table shows, so
"12.40 bps effective, 11.21 kept" is a claim anyone can check rather than one
they have to take on faith.

    cd backend && python scripts/benchmark.py
    cd backend && python scripts/benchmark.py --markdown   # the README's table
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.sdk import Desk, Shock, simulate  # noqa: E402

# The configuration behind every number in the README. Changing it means
# regenerating the table, which is the point of keeping it in one place.
SEED = 42
STEPS = 1000
SHOCK = Shock(step=500, pct=-8.0)
DESKS = [
    Desk("mm", "MARKET_MAKER", latency_in_us=600, latency_out_us=1200, latency_jitter_us=300),
    Desk("momentum", "MOMENTUM", latency_in_us=400, latency_out_us=500),
    Desk("reversion", "MEAN_REVERSION", latency_in_us=500, latency_out_us=700),
    Desk("flow", "NOISE_TRADER", activity=0.5, latency_in_us=300, latency_out_us=400),
    Desk("options", "OPTIONS_MAKER", client_intensity=0.25, hedge_band=20.0,
         latency_in_us=800, latency_out_us=1100),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--markdown", action="store_true", help="print the README table")
    args = parser.parse_args()

    started = time.perf_counter()
    run = simulate(steps=STEPS, seed=SEED, shocks=[SHOCK], agents=DESKS)
    elapsed = time.perf_counter() - started

    market = run.spread()
    maker = next((r for r in market if r["agent_id"] == "mm" and r["role"] == "maker"), None)
    races = run.races()
    worst = max((r["margin_us"] for r in races), default=0)

    if args.markdown:
        print("| Question | Answer in this run |")
        print("|---|---|")
        if maker:
            print(
                f"| How much of the spread did the maker **keep**? "
                f"| `{maker['effective_bps']:.2f} bps` quoted -> "
                f"`{maker['realised_bps']:.2f} bps` kept, "
                f"`{maker['impact_bps']:.2f} bps` to informed flow |"
            )
        print(
            f"| What did **being slow** cost? | `{len(races)}` contested fills; "
            f"worst cancel `{worst / 1000:.1f} ms` late |"
        )
        print(
            f"| Did any money appear from nowhere? "
            f"| `sum PnL + fees = {run.conservation:.1e}` across {len(DESKS)} desks |"
        )
        return 0

    print(f"\nseed {SEED} - {STEPS} steps - {len(DESKS)} desks - {SHOCK.pct}% at step {SHOCK.step}")
    print(f"wall clock: {elapsed:.2f}s ({STEPS / elapsed:,.0f} steps/s)\n")
    print(f"{'trades':<28}{run.summary['total_trades']:>12,}")
    print(f"{'orders':<28}{run.summary['total_orders']:>12,}")
    print(f"{'steps with no two-sided book':<28}{run.summary['steps_with_no_two_sided_book']:>12,}")
    print(f"{'contested fills':<28}{len(races):>12,}")
    print(f"{'conservation residual':<28}{run.conservation:>12.2e}\n")

    print(f"{'desk':<12}{'total pnl':>14}{'sharpe':>10}{'fills':>8}{'end inv':>10}")
    for row in sorted(run.agents(), key=lambda r: -r["total_pnl"]):
        sharpe = row.get("sharpe_ratio")
        print(
            f"{row['agent_id']:<12}{row['total_pnl']:>14,.2f}"
            f"{(f'{sharpe:.2f}' if sharpe is not None else '-'):>10}"
            f"{row['fill_count']:>8}{row['final_inventory']:>10,.0f}"
        )

    if maker:
        print(
            f"\nmaker spread at {len(market) and 'the default horizon'}: "
            f"effective {maker['effective_bps']:.2f} bps = "
            f"realised {maker['realised_bps']:.2f} + impact {maker['impact_bps']:.2f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
