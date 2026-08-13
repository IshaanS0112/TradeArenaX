import type { Comparison } from "../api/types";
import { LineChart, type Series } from "./LineChart";
import { Panel } from "./Panel";
import { AGENT_LABEL, num, pct, pnlClass, ratePct, signed } from "./format";

export function StrategyComparison({ comparison }: { comparison: Comparison }) {
  const market: Series[] = [
    {
      label: "Reference price (GBM)",
      color: "#64748b",
      points: comparison.market.map((p) => ({ x: p.step, y: p.reference_price })),
    },
    {
      label: "Book mid",
      color: "#38bdf8",
      points: comparison.market
        .filter((p) => p.mid_price !== null)
        .map((p) => ({ x: p.step, y: p.mid_price as number })),
    },
  ];

  const residualIsClean = Math.abs(comparison.pnl_conservation_residual) < 1e-6;

  return (
    <div className="space-y-6">
      <Panel
        title="Market"
        subtitle="The synthetic reference path and the mid price the book actually produced. They should track each other; the gap is what the maker's quotes did not absorb."
      >
        <LineChart series={market} markers={comparison.shock_steps} yLabel="Price" />
      </Panel>

      <Panel
        title="Strategy comparison"
        subtitle={`${comparison.steps_run} steps · ranked by total PnL`}
      >
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-edge text-left text-muted">
                <Th>Agent</Th>
                <Th right>Total PnL</Th>
                <Th right>Realized</Th>
                <Th right>Unreal.</Th>
                <Th right title="Annualised. See the note below the table.">
                  Sharpe
                </Th>
                <Th right title="Sharpe with only downside dispersion in the denominator">
                  Sortino
                </Th>
                <Th right>Max DD</Th>
                <Th right>Win rate</Th>
                <Th right>Trips</Th>
                <Th right>Fills</Th>
                <Th right>Final inv.</Th>
                <Th right title="Peak |inventory| / limit. Above 1.00 forced a liquidation.">
                  Risk
                </Th>
              </tr>
            </thead>
            <tbody>
              {comparison.rows.map((row) => (
                <tr key={row.agent_id} className="border-b border-edge/50 last:border-0">
                  <td className="py-2 pr-3">
                    <div className="text-slate-200">{row.name}</div>
                    <div className="text-[11px] text-muted">
                      {AGENT_LABEL[row.agent_type] ?? row.agent_type}
                    </div>
                  </td>
                  <Td right className={pnlClass(row.total_pnl)}>
                    {signed(row.total_pnl)}
                  </Td>
                  <Td right className={pnlClass(row.realized_pnl)}>
                    {signed(row.realized_pnl)}
                  </Td>
                  <Td right className={pnlClass(row.unrealized_pnl)}>
                    {signed(row.unrealized_pnl)}
                  </Td>
                  <Td right>{num(row.sharpe_ratio)}</Td>
                  <Td right>{num(row.sortino_ratio)}</Td>
                  <Td right>{pct(row.max_drawdown_pct, 2)}</Td>
                  <Td right>{ratePct(row.win_rate)}</Td>
                  <Td right>{num(row.closed_round_trips, 0)}</Td>
                  <Td right>{num(row.fill_count, 0)}</Td>
                  <Td right>{num(row.final_inventory, 0)}</Td>
                  <Td
                    right
                    className={
                      (row.max_inventory_risk_score ?? 0) > 1
                        ? "text-negative"
                        : "text-slate-200"
                    }
                  >
                    {num(row.max_inventory_risk_score, 2)}
                  </Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* The conservation residual is displayed rather than merely checked in a
            test, because it is the one number that tells a reader whether the
            rest of the table can be trusted at all. */}
        <div className="mt-4 space-y-2 text-xs">
          <p className={residualIsClean ? "text-muted" : "text-negative"}>
            PnL conservation check:{" "}
            <span className="num">
              {comparison.pnl_conservation_residual.toExponential(2)}
            </span>{" "}
            {residualIsClean
              ? "— agents' PnL sums to minus the fees collected, as a closed market must."
              : "— non-zero residual. This is an accounting bug, not a profit."}
            {comparison.total_fees_collected > 0 && (
              <>
                {" "}
                Fees collected:{" "}
                <span className="num">{num(comparison.total_fees_collected)}</span>.
              </>
            )}
          </p>
          <p className="text-muted">
            Sharpe is annualised by <span className="font-mono">sqrt(steps_per_year)</span>.
            Over a short run that is a large extrapolation; compare agents by rank
            rather than by absolute level, and read the per-agent notes.
          </p>
        </div>

        {comparison.warnings.length > 0 && (
          <ul className="mt-3 space-y-1 text-xs text-caution">
            {comparison.warnings.map((w) => (
              <li key={w}>· {w}</li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function Th({
  children,
  right,
  title,
}: {
  children: React.ReactNode;
  right?: boolean;
  title?: string;
}) {
  return (
    <th
      title={title}
      className={`py-2 font-medium ${right ? "pl-3 text-right" : "pr-3"} ${
        title ? "cursor-help underline decoration-dotted underline-offset-2" : ""
      }`}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  right,
  className = "text-slate-200",
}: {
  children: React.ReactNode;
  right?: boolean;
  className?: string;
}) {
  return (
    <td className={`num py-2 ${right ? "pl-3 text-right" : "pr-3"} ${className}`}>
      {children}
    </td>
  );
}
