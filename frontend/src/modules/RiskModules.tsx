import type { ModuleData } from "./types";
import { EmptyState, ErrorState, Skeleton, Stat, Tag } from "../design/primitives";
import { num } from "../components/format";

/** The invariant, shown rather than claimed. */
export function ConservationCheck({ data }: { data: ModuleData }) {
  if (data.errors.comparison) {
    return <ErrorState endpoint={data.endpoints.comparison} message={data.errors.comparison} />;
  }
  if (data.loading.comparison && !data.comparison) return <Skeleton lines={5} />;
  const comparison = data.comparison;
  const summary = data.simulation?.run_summary as
    | { steps_with_no_two_sided_book?: number; realized_volatility?: number | null; configured_volatility?: number }
    | undefined;
  if (!comparison) return <EmptyState>Run the simulation to check the invariant.</EmptyState>;

  const residual = comparison.pnl_conservation_residual;
  const holds = Math.abs(residual) < 1e-6;

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center gap-2">
        <Tag tone={holds ? "bid" : "ask"}>{holds ? "invariant holds" : "residual"}</Tag>
        <span className="num text-xs text-secondary">
          Σ agent PnL = {residual.toExponential(2)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Stat label="Fees collected" value={num(comparison.total_fees_collected, 2)} />
        <Stat label="Steps" value={num(comparison.steps_run, 0)} />
        <Stat
          label="One-sided steps"
          value={num(summary?.steps_with_no_two_sided_book ?? null, 0)}
          hint="no two-sided book, so no mid"
          tone={(summary?.steps_with_no_two_sided_book ?? 0) > 0 ? "warn" : "default"}
        />
        <Stat
          label="σ configured / realised"
          value={`${num(summary?.configured_volatility ?? null, 2)} / ${num(
            summary?.realized_volatility ?? null,
            2,
          )}`}
        />
      </div>

      {comparison.warnings.length > 0 ? (
        <ul className="scroll-thin min-h-0 flex-1 space-y-1 overflow-auto text-2xs text-warn">
          {comparison.warnings.map((w) => (
            <li key={w}>· {w}</li>
          ))}
        </ul>
      ) : (
        <p className="text-2xs text-muted">No warnings raised by this run.</p>
      )}
    </div>
  );
}

/** Step-by-step tape: the debugging view, and the one that makes a shock concrete — spread. */
export function MarketTape({ data }: { data: ModuleData }) {
  if (data.errors.comparison) {
    return <ErrorState endpoint={data.endpoints.comparison} message={data.errors.comparison} />;
  }
  if (data.loading.comparison && !data.comparison) return <Skeleton lines={8} />;
  const market = data.comparison?.market ?? [];
  if (!market.length) return <EmptyState>No steps recorded yet.</EmptyState>;

  // Newest first: a tape is read from the top.
  const rows = [...market].reverse().slice(0, 400);

  return (
    <div className="scroll-thin h-full overflow-auto">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 bg-panel text-2xs uppercase tracking-wide text-muted">
          <tr className="border-b border-edge">
            <th className="py-2 pr-3 text-left font-medium">Step</th>
            <th className="py-2 pr-3 text-right font-medium">Ref</th>
            <th className="py-2 pr-3 text-right font-medium">Mid</th>
            <th className="py-2 pr-3 text-right font-medium">Spread</th>
            <th className="py-2 pr-3 text-right font-medium">σ</th>
            <th className="py-2 text-right font-medium">Trades</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.step}
              onClick={() => data.setStep(row.step)}
              className={`cursor-pointer border-b border-edge/40 hover:bg-raised ${
                row.step === data.step ? "bg-raised" : ""
              }`}
            >
              <td className="num py-1 pr-3">
                {row.step}
                {row.shock_fired && <span className="ml-1 text-warn">shock</span>}
              </td>
              <td className="num py-1 pr-3 text-right">{num(row.reference_price, 2)}</td>
              <td className="num py-1 pr-3 text-right">{num(row.mid_price, 2)}</td>
              <td className="num py-1 pr-3 text-right">{num(row.spread, 3)}</td>
              <td className="num py-1 pr-3 text-right">{num(row.volatility, 2)}</td>
              <td className="num py-1 text-right">{row.trade_count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
