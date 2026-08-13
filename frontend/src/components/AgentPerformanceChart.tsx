import type { AgentPerformance } from "../api/types";
import { LineChart, type Series } from "./LineChart";
import { Empty, Notes, Panel } from "./Panel";
import { AGENT_COLOR, AGENT_LABEL, num, pnlClass, ratePct, signed } from "./format";

/** PnL and inventory for every agent, plus the realized/unrealized split.
 *
 * Inventory is charted next to PnL rather than on a separate tab because the two
 * only make sense together: a market maker's drawdown is almost always the
 * moment its inventory ran one-directional, and that is invisible if you look at
 * the PnL curve alone.
 */
export function AgentPerformanceChart({
  performances,
  shockSteps,
}: {
  performances: AgentPerformance[];
  shockSteps: number[];
}) {
  if (!performances.length) {
    return (
      <Panel title="Agent performance">
        <Empty>Run the simulation to see PnL and inventory paths.</Empty>
      </Panel>
    );
  }

  const pnl: Series[] = performances.map((p) => ({
    label: `${p.name} PnL`,
    color: AGENT_COLOR[p.agent_type] ?? "#94a3b8",
    points: p.series.map((s) => ({ x: s.step, y: s.total_pnl })),
  }));

  const inventory: Series[] = performances.map((p) => ({
    label: `${p.name} inventory`,
    color: AGENT_COLOR[p.agent_type] ?? "#94a3b8",
    points: p.series.map((s) => ({ x: s.step, y: s.inventory })),
  }));

  return (
    <div className="space-y-6">
      <Panel
        title="Total PnL by agent"
        subtitle="Realized round-trip PnL plus mark-to-market on open inventory, per step"
      >
        <LineChart series={pnl} markers={shockSteps} zeroLine yLabel="PnL" />
      </Panel>

      <Panel
        title="Inventory by agent"
        subtitle="Signed position. A drawdown and a one-directional inventory are usually the same event."
      >
        <LineChart series={inventory} markers={shockSteps} zeroLine yLabel="Shares" />
      </Panel>

      <div className="grid gap-4 md:grid-cols-3">
        {performances.map((p) => (
          <Panel
            key={p.agent_id}
            title={p.name}
            subtitle={AGENT_LABEL[p.agent_type] ?? p.agent_type}
          >
            <dl className="space-y-1.5 text-xs">
              <Row label="Total PnL" value={signed(p.metrics.total_pnl)} accent={p.metrics.total_pnl} />
              <Row label="Realized" value={signed(p.metrics.realized_pnl)} accent={p.metrics.realized_pnl} />
              <Row
                label="Unrealized"
                value={signed(p.metrics.unrealized_pnl)}
                accent={p.metrics.unrealized_pnl}
              />
              <Row label="Fills" value={num(p.metrics.fill_count, 0)} />
              <Row label="Round trips" value={num(p.metrics.closed_round_trips, 0)} />
              <Row label="Win rate" value={ratePct(p.metrics.win_rate)} />
              <Row label="Peak |inventory|" value={num(p.metrics.peak_inventory_abs, 0)} />
              <Row
                label="Peak risk score"
                value={num(p.metrics.max_inventory_risk_score, 2)}
                accent={
                  (p.metrics.max_inventory_risk_score ?? 0) > 1 ? -1 : undefined
                }
              />
            </dl>

            {p.risk_flags.length > 0 && (
              <div className="mt-3 rounded border border-caution/40 bg-caution/5 p-2">
                <p className="text-[11px] font-semibold uppercase tracking-wide text-caution">
                  Risk events
                </p>
                <ul className="mt-1 space-y-0.5 text-[11px] text-caution/90">
                  {p.risk_flags.slice(0, 4).map((flag) => (
                    <li key={flag}>· {flag}</li>
                  ))}
                  {p.risk_flags.length > 4 && (
                    <li className="text-muted">
                      + {p.risk_flags.length - 4} more
                    </li>
                  )}
                </ul>
              </div>
            )}

            <Notes notes={p.metrics.notes ?? []} />
          </Panel>
        ))}
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: number | null;
}) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className={`num ${accent === undefined ? "text-slate-200" : pnlClass(accent)}`}>
        {value}
      </dd>
    </div>
  );
}
