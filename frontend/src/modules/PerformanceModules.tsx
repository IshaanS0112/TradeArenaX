import type { ModuleData } from "./types";
import { AxisNote, EmptyState, ErrorState, Skeleton } from "../design/primitives";
import { LineChart, type Series } from "../components/LineChart";
import { AGENT_COLOR, num, pnlClass, ratePct, signed } from "../components/format";

function seriesFor(
  data: ModuleData,
  pick: (point: { total_pnl: number; inventory: number }) => number,
): Series[] {
  return data.performances.map((performance, index) => ({
    label: performance.name,
    color:
      AGENT_COLOR[performance.agent_type] ??
      SERIES_FALLBACK[index % SERIES_FALLBACK.length] ??
      "var(--accent)",
    points: performance.series.map((p) => ({ x: p.step, y: pick(p) })),
  }));
}

// Five agents of the same archetype would otherwise all draw in one colour.
const SERIES_FALLBACK = [
  "var(--accent)",
  "var(--info)",
  "var(--warn)",
  "var(--bid)",
  "var(--ask)",
];

function distinctColours(base: Series[]): Series[] {
  return base.map((s, i) => ({
    ...s,
    color: SERIES_FALLBACK[i % SERIES_FALLBACK.length] ?? "var(--accent)",
  }));
}

export function PnLCurve({ data }: { data: ModuleData }) {
  if (data.errors.performance) {
    return <ErrorState endpoint={data.endpoints.performance} message={data.errors.performance} />;
  }
  if (data.loading.performance && !data.performances.length) return <Skeleton lines={6} />;
  if (!data.performances.length) return <EmptyState>No agent series yet.</EmptyState>;

  const shocks = data.comparison?.shock_steps ?? [];
  return (
    <div>
      <LineChart
        series={distinctColours(seriesFor(data, (p) => p.total_pnl))}
        markers={shocks}
        height={230}
        zeroLine
      />
      <AxisNote>
        Total PnL (realised + mark-to-market) per agent against step. Amber marks the
        volatility shock. Sum across agents is zero by construction — the conservation
        module shows the residual.
      </AxisNote>
    </div>
  );
}

export function InventoryCurve({ data }: { data: ModuleData }) {
  if (data.errors.performance) {
    return <ErrorState endpoint={data.endpoints.performance} message={data.errors.performance} />;
  }
  if (data.loading.performance && !data.performances.length) return <Skeleton lines={6} />;
  if (!data.performances.length) return <EmptyState>No agent series yet.</EmptyState>;

  return (
    <div>
      <LineChart
        series={distinctColours(seriesFor(data, (p) => p.inventory))}
        markers={data.comparison?.shock_steps ?? []}
        height={230}
        zeroLine
      />
      <AxisNote>
        Signed inventory per agent. A market maker that never mean-reverts to zero is
        carrying directional risk it was not paid to take.
      </AxisNote>
    </div>
  );
}

export function MarketChart({ data }: { data: ModuleData }) {
  if (data.errors.comparison) {
    return <ErrorState endpoint={data.endpoints.comparison} message={data.errors.comparison} />;
  }
  if (data.loading.comparison && !data.comparison) return <Skeleton lines={6} />;
  const market = data.comparison?.market ?? [];
  if (!market.length) return <EmptyState>No market series yet.</EmptyState>;

  const series: Series[] = [
    {
      label: "Reference (GBM)",
      color: "var(--fg-secondary)",
      points: market.map((m) => ({ x: m.step, y: m.reference_price })),
    },
    {
      label: "Mid",
      color: "var(--accent)",
      points: market
        .filter((m) => m.mid_price !== null)
        .map((m) => ({ x: m.step, y: m.mid_price as number })),
    },
  ];

  return (
    <div>
      <LineChart
        series={series}
        markers={data.comparison?.shock_steps ?? []}
        height={230}
      />
      <AxisNote>
        The synthetic reference path and the book's own mid. Where they separate, the
        book is not tracking the process — which is what a thin book looks like.
      </AxisNote>
    </div>
  );
}

export function AgentMatrix({ data }: { data: ModuleData }) {
  if (data.errors.comparison) {
    return <ErrorState endpoint={data.endpoints.comparison} message={data.errors.comparison} />;
  }
  if (data.loading.comparison && !data.comparison) return <Skeleton lines={6} />;
  const rows = data.comparison?.rows ?? [];
  if (!rows.length) return <EmptyState>No results yet — run the simulation.</EmptyState>;

  return (
    <div className="scroll-thin h-full overflow-auto">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 bg-panel text-2xs uppercase tracking-wide text-muted">
          <tr className="border-b border-edge">
            <th className="py-2 pr-3 text-left font-medium">Agent</th>
            <th className="py-2 pr-3 text-right font-medium">Total PnL</th>
            <th className="py-2 pr-3 text-right font-medium">Realised</th>
            <th className="py-2 pr-3 text-right font-medium">Sharpe</th>
            <th className="py-2 pr-3 text-right font-medium">Max DD</th>
            <th className="py-2 pr-3 text-right font-medium">Win rate</th>
            <th className="py-2 pr-3 text-right font-medium">Fills</th>
            <th className="py-2 text-right font-medium">End inv.</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.agent_id} className="border-b border-edge/50 hover:bg-raised">
              <td className="py-1.5 pr-3">
                <div className="text-primary">{row.name}</div>
                <div className="text-2xs text-muted">{row.agent_type.replace("_", " ").toLowerCase()}</div>
              </td>
              <td className={`num py-1.5 pr-3 text-right ${pnlClass(row.total_pnl)}`}>
                {signed(row.total_pnl)}
              </td>
              <td className={`num py-1.5 pr-3 text-right ${pnlClass(row.realized_pnl)}`}>
                {signed(row.realized_pnl)}
              </td>
              <td className="num py-1.5 pr-3 text-right">{num(row.sharpe_ratio, 2)}</td>
              <td className="num py-1.5 pr-3 text-right">{num(row.max_drawdown_pct, 1)}</td>
              <td className="num py-1.5 pr-3 text-right">{ratePct(row.win_rate)}</td>
              <td className="num py-1.5 pr-3 text-right">{num(row.fill_count, 0)}</td>
              <td className="num py-1.5 text-right">{num(row.final_inventory, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <AxisNote>
        A dash is a metric the data does not support — an agent with no closed round
        trip has no win rate, and printing 0% would state something false. Point
        estimates from one path: confidence intervals arrive with ensemble runs.
      </AxisNote>
    </div>
  );
}
