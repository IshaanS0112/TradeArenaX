import type { ModuleData } from "./types";
import { AxisNote, EmptyState, ErrorState, Skeleton, Stat, Tag } from "../design/primitives";
import { num, signed } from "../components/format";

/** Where the maker's money came from, and what informed flow took back. */
export function SpreadDecomposition({ data }: { data: ModuleData }) {
  if (data.errors.microstructure) {
    return (
      <ErrorState endpoint={data.endpoints.microstructure} message={data.errors.microstructure} />
    );
  }
  if (data.loading.microstructure && !data.microstructure) return <Skeleton lines={6} />;
  const micro = data.microstructure;
  if (!micro) return <EmptyState>No trades to decompose yet.</EmptyState>;

  const horizon = String(micro.default_horizon_steps);
  const rows = (micro.by_agent[horizon] ?? []).filter((r) => r.role === "maker");
  const market = micro.market[horizon];
  if (!rows.length) return <EmptyState>No passive fills at this horizon.</EmptyState>;

  const scale = Math.max(
    ...rows.flatMap((r) => [Math.abs(r.realised_bps ?? 0), Math.abs(r.impact_bps ?? 0)]),
    1,
  );

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 grid grid-cols-3 gap-3">
        <Stat
          label="Effective"
          value={`${num(market?.effective_bps, 2)} bps`}
          hint="quoted spread earned"
        />
        <Stat
          label="Realised"
          value={`${num(market?.realised_bps, 2)} bps`}
          hint={`kept after ${horizon} steps`}
          tone={(market?.realised_bps ?? 0) >= 0 ? "positive" : "negative"}
        />
        <Stat
          label="Impact"
          value={`${num(market?.impact_bps, 2)} bps`}
          hint="taken by informed flow"
          tone={(market?.impact_bps ?? 0) > 0 ? "negative" : "positive"}
        />
      </div>

      <div className="scroll-thin min-h-0 flex-1 space-y-2.5 overflow-auto pr-1">
        {rows.map((row) => {
          const realised = row.realised_bps ?? 0;
          const impact = row.impact_bps ?? 0;
          // A diverging bar around a zero line: what the maker kept to the right, what impact took.
          const keptWidth = (Math.abs(realised) / scale) * 50;
          const lostWidth = (Math.abs(impact) / scale) * 50;
          return (
            <div key={`${row.agent_id}-${row.role}`} className="text-2xs">
              <div className="flex items-baseline gap-2">
                <span className="truncate text-primary">{row.name ?? row.agent_id}</span>
                <span className="text-muted">{row.trade_count} fills</span>
                <span className="num ml-auto text-muted">
                  gross {signed(row.effective_bps, 1)}
                </span>
                <span
                  className={`num w-16 text-right ${
                    realised >= 0 ? "text-bid" : "text-ask"
                  }`}
                >
                  net {signed(realised, 1)}
                </span>
              </div>
              <div className="relative mt-1 h-4 w-full rounded-input bg-raised">
                <div className="absolute inset-y-0 left-1/2 w-px bg-edge-focus" />
                {/* impact, left of zero */}
                <div
                  className="absolute inset-y-0 bg-ask/70"
                  style={{ right: "50%", width: `${lostWidth}%` }}
                  title={`impact ${impact.toFixed(2)} bps`}
                />
                {/* what survived, right of zero */}
                <div
                  className={`absolute inset-y-0 ${realised >= 0 ? "bg-bid/70" : "bg-ask/40"}`}
                  style={{ left: "50%", width: `${keptWidth}%` }}
                  title={`realised ${realised.toFixed(2)} bps`}
                />
              </div>
            </div>
          );
        })}
      </div>

      <AxisNote>
        Left of the line is what price impact took over {horizon} steps; right is what
        the maker kept. A red bar on the right means impact exceeded the gross spread -
        the quote lost money before it was even unwound.
      </AxisNote>
    </div>
  );
}

/** One contested fill, drawn as a timeline. */
export function LatencyRaceModule({ data }: { data: ModuleData }) {
  if (data.errors.latency) {
    return <ErrorState endpoint={data.endpoints.latency} message={data.errors.latency} />;
  }
  if (data.loading.latency && !data.latency) return <Skeleton lines={6} />;
  const latency = data.latency;
  if (!latency) return <EmptyState>No latency data for this run.</EmptyState>;

  if (!latency.races.length) {
    return (
      <div className="flex h-full flex-col gap-3">
        <EmptyState>
          No contested fills: every agent in this run has the same (zero) latency, so no
          cancel was ever in flight when an order arrived. Give the maker a
          <span className="num"> latency_out_us</span> and re-run.
        </EmptyState>
        <div className="scroll-thin max-h-28 overflow-auto text-2xs text-muted">
          {Object.entries(latency.profiles).map(([agent, profile]) => (
            <div key={agent} className="num flex gap-3">
              <span className="truncate">{agent.slice(0, 8)}</span>
              <span>in {profile.latency_in_us}µs</span>
              <span>out {profile.latency_out_us}µs</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // The widest race is the clearest one to draw.
  const race = [...latency.races].sort((a, b) => b.margin_us - a.margin_us)[0]!;
  const start = Math.min(race.maker_decided_us, race.cancel_issued_us);
  const end = Math.max(race.cancel_arrival_us, race.fill_us);
  const span = Math.max(end - start, 1);
  const at = (us: number) => ((us - start) / span) * 100;

  const marks = [
    { label: "maker decided", us: race.maker_decided_us, tone: "bg-secondary" },
    { label: "cancel sent", us: race.cancel_issued_us, tone: "bg-accent" },
    { label: "fill happens", us: race.fill_us, tone: "bg-ask" },
    { label: "cancel arrives", us: race.cancel_arrival_us, tone: "bg-warn" },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="mb-3 grid grid-cols-3 gap-3">
        <Stat label="Step" value={num(race.step, 0)} />
        <Stat
          label="Too late by"
          value={`${num(race.margin_us / 1000, 1)} ms`}
          tone="negative"
          hint="cancel arrival minus fill"
        />
        <Stat
          label="Adverse fills"
          value={num(latency.adverse_fills[race.maker_agent_id] ?? 0, 0)}
          hint={`of ${latency.races_recorded} recorded`}
        />
      </div>

      {/* One row per event rather than one axis with four labels on it: at
          these delays two of the four land within a pixel of each other, and
          overlapping labels are worse than no chart. */}
      <div className="space-y-1.5">
        {marks.map((mark) => (
          <div key={mark.label} className="flex items-center gap-2 text-2xs">
            <span className="w-24 shrink-0 text-muted">{mark.label}</span>
            <div className="relative h-2 flex-1 rounded-input bg-raised">
              <div
                className={`absolute inset-y-0 left-0 rounded-input ${mark.tone}`}
                style={{ width: `${Math.max(at(mark.us), 1.5)}%` }}
              />
            </div>
            <span className="num w-16 shrink-0 text-right text-secondary">
              +{num((mark.us - start) / 1000, 1)} ms
            </span>
          </div>
        ))}
      </div>

      <div className="mt-auto grid grid-cols-2 gap-2 border-t border-edge pt-2 text-2xs">
        <div>
          <span className="text-muted">Maker </span>
          <span className="text-primary">{race.maker_name ?? race.maker_agent_id}</span>
        </div>
        <div className="text-right">
          <span className="text-muted">Taker </span>
          <span className="text-primary">{race.taker_name ?? race.taker_agent_id}</span>
        </div>
        <div className="num">
          {num(race.quantity, 0)} @ {num(race.price, 2)}
        </div>
        <div className="text-right">
          <Tag tone="ask">adverse fill</Tag>
        </div>
      </div>

      <AxisNote>
        The maker pulled this quote before the trade happened. Its cancel was still on the
        wire when the aggressor's order reached the book.
      </AxisNote>
    </div>
  );
}

/** Gamma, theta and hedging slippage for a delta-hedged options book. */
export function GreeksAttribution({ data }: { data: ModuleData }) {
  if (data.errors.greeks) {
    return <ErrorState endpoint={data.endpoints.greeks} message={data.errors.greeks} />;
  }
  if (data.loading.greeks && !data.greeks) return <Skeleton lines={6} />;
  const greeks = data.greeks;
  if (!greeks || !Object.keys(greeks.agents).length) {
    return (
      <EmptyState>
        No options maker in this run, so there is no hedged book to attribute. Add an
        OPTIONS_MAKER agent and re-run.
      </EmptyState>
    );
  }

  const [agentId, attribution] = Object.entries(greeks.agents)[0]!;
  const snapshots = attribution.snapshots;
  const gammaSeries = snapshots.map((s) => s.gamma_pnl);
  const running: number[] = [];
  gammaSeries.reduce((total, value) => {
    const next = total + value;
    running.push(next);
    return next;
  }, 0);

  const W = 1000;
  const H = 140;
  const max = Math.max(...running.map(Math.abs), 1e-9);
  const path = running
    .map((value, index) => {
      const x = (index / Math.max(running.length - 1, 1)) * W;
      const y = H / 2 - (value / max) * (H / 2 - 6);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-baseline gap-2">
        <span className="text-xs text-primary">{greeks.names[agentId] ?? agentId}</span>
        <span className="num ml-auto text-2xs text-muted">
          {attribution.hedge_trades} hedge trades
        </span>
      </div>

      <div className="grid grid-cols-4 gap-2">
        <Stat
          label="Gamma"
          value={signed(attribution.gamma_pnl, 1)}
          tone={attribution.gamma_pnl >= 0 ? "positive" : "negative"}
        />
        <Stat
          label="Theta"
          value={signed(attribution.theta_pnl, 1)}
          tone={attribution.theta_pnl >= 0 ? "positive" : "negative"}
        />
        <Stat label="Vega" value={signed(attribution.vega_pnl, 1)} />
        <Stat
          label="Slippage"
          value={signed(attribution.hedge_slippage, 1)}
          tone="negative"
        />
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="mt-3 h-auto w-full" role="img"
        aria-label="Cumulative gamma P&L over the run">
        <line x1="0" x2={W} y1={H / 2} y2={H / 2} stroke="var(--border)" />
        {path && <polyline points={path} fill="none" stroke="var(--info)" strokeWidth="2" />}
      </svg>

      <AxisNote>
        Cumulative gamma P&L. A delta-hedged book still makes or loses money as
        ½·Γ·S²·(σ_realised² − σ_implied²)·dt — premium of{" "}
        {signed(attribution.option_premium, 0)} is carried separately, outside the equity
        accounting, so the conservation invariant stays intact.
      </AxisNote>
    </div>
  );
}

/** Microprice against mid, and which one actually forecasts better. */
export function MicropriceModule({ data }: { data: ModuleData }) {
  if (data.errors.microprice) {
    return <ErrorState endpoint={data.endpoints.microprice} message={data.errors.microprice} />;
  }
  if (data.loading.microprice && !data.microprice) return <Skeleton lines={6} />;
  const micro = data.microprice;
  if (!micro || micro.steps.length < 2) {
    return <EmptyState>Not enough two-sided book to compute a microprice.</EmptyState>;
  }

  const midRmse = micro.forecast.mid_rmse;
  const microRmse = micro.forecast.microprice_rmse;
  const microWins = midRmse !== null && microRmse !== null && microRmse < midRmse;

  const points = micro.steps
    .map((step, index) => ({
      step,
      mid: micro.mid[index],
      micro: micro.microprice[index],
      imbalance: micro.imbalance[index] ?? 0.5,
    }))
    .filter((p) => p.mid !== null && p.micro !== null)
    .slice(-400);

  const W = 1000;
  const H = 150;
  const values = points.flatMap((p) => [p.mid as number, p.micro as number]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const x = (i: number) => (i / Math.max(points.length - 1, 1)) * W;
  const y = (v: number) => H - 8 - ((v - min) / range) * (H - 16);

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 grid grid-cols-3 gap-3">
        <Stat label="Mid RMSE" value={num(midRmse, 5)} hint="one step ahead" />
        <Stat
          label="Microprice RMSE"
          value={num(microRmse, 5)}
          tone={microWins ? "positive" : "warn"}
        />
        <Stat
          label="Verdict"
          value={microWins ? "microprice" : "mid"}
          hint="better fair value here"
        />
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
        aria-label="Mid price against microprice">
        <polyline
          points={points.map((p, i) => `${x(i)},${y(p.mid as number)}`).join(" ")}
          fill="none"
          stroke="var(--fg-secondary)"
          strokeWidth="1.5"
        />
        <polyline
          points={points.map((p, i) => `${x(i)},${y(p.micro as number)}`).join(" ")}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="1.5"
        />
      </svg>

      <AxisNote>
        Grey mid, cyan microprice. In this market imbalance does not predict the next move —
        almost all resting size belongs to one inventory-skewing maker — so the microprice
        is not the better fair value. That is a measured result, and a test pins it.
      </AxisNote>
    </div>
  );
}
