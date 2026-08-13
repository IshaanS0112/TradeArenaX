import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, ApiError, optional } from "../api/client";
import type {
  AgentPerformance,
  Comparison,
  OrderBookSnapshot,
  Simulation,
} from "../api/types";
import { AgentPerformanceChart } from "../components/AgentPerformanceChart";
import { OrderBookView } from "../components/OrderBookView";
import { Panel } from "../components/Panel";
import { StrategyComparison } from "../components/StrategyComparison";
import { num } from "../components/format";

type Tab = "comparison" | "agents" | "book";

export default function SimulationDetail() {
  const { id = "" } = useParams();
  const [sim, setSim] = useState<Simulation | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [performances, setPerformances] = useState<AgentPerformance[]>([]);
  const [book, setBook] = useState<OrderBookSnapshot | null>(null);
  const [bookStep, setBookStep] = useState(0);
  const [tab, setTab] = useState<Tab>("comparison");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const simulation = await api.getSimulation(id);
        if (cancelled) return;
        setSim(simulation);

        const [cmp, ...perfs] = await Promise.all([
          optional(api.comparison(id)),
          ...simulation.agents.map((a) => optional(api.agentPerformance(id, a.id))),
        ]);
        if (cancelled) return;
        setComparison(cmp);
        setPerformances(perfs.filter((p): p is AgentPerformance => p !== null));

        if (simulation.duration_steps > 0) {
          setBookStep(simulation.duration_steps);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e));
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const loadBook = useCallback(
    async (step: number) => {
      if (!step) return;
      const snapshot = await optional(api.orderBook(id, step));
      setBook(snapshot);
    },
    [id],
  );

  useEffect(() => {
    void loadBook(bookStep);
  }, [bookStep, loadBook]);

  if (error) {
    return (
      <p className="rounded border border-negative/40 bg-negative/10 p-3 text-sm text-negative">
        {error}
      </p>
    );
  }
  if (!sim) return <p className="text-sm text-muted">Loading…</p>;

  const summary = "total_trades" in sim.run_summary ? sim.run_summary : null;
  const shockSteps = (sim.volatility_shock_config.shocks ?? []).map((s) => s.step);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <Link to="/" className="text-xs text-muted hover:text-accent">
            ← All simulations
          </Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight text-slate-100">
            {sim.name}
          </h1>
          <p className="mt-1 text-xs text-muted">
            seed {sim.price_process_config.random_seed} · sigma{" "}
            {num(sim.price_process_config.volatility, 2)} · drift{" "}
            {num(sim.price_process_config.drift, 2)} · S0{" "}
            {num(sim.price_process_config.initial_price)} · {sim.duration_steps} steps
          </p>
        </div>
      </div>

      {sim.status !== "COMPLETED" && (
        <p className="rounded border border-caution/40 bg-caution/10 p-3 text-sm text-caution">
          This simulation has not been run yet.
        </p>
      )}

      {summary && (
        <Panel title="Run summary">
          <div className="grid gap-4 text-xs sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Steps" value={num(summary.steps_run, 0)} />
            <Stat label="Trades" value={num(summary.total_trades, 0)} />
            <Stat label="Orders" value={num(summary.total_orders, 0)} />
            <Stat
              label="Sigma configured"
              value={num(summary.configured_volatility, 3)}
            />
            <Stat label="Sigma realized" value={num(summary.realized_volatility, 3)} />
            <Stat
              label="One-sided steps"
              value={num(summary.steps_with_no_two_sided_book, 0)}
            />
          </div>
          {summary.warnings.length > 0 && (
            <ul className="mt-4 space-y-1 text-xs text-caution">
              {summary.warnings.map((w) => (
                <li key={w}>· {w}</li>
              ))}
            </ul>
          )}
        </Panel>
      )}

      <nav className="flex gap-1 border-b border-edge text-sm">
        {(
          [
            ["comparison", "Comparison"],
            ["agents", "Agents"],
            ["book", "Order book"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-3 py-2 ${
              tab === key
                ? "border-accent text-accent"
                : "border-transparent text-muted hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "comparison" &&
        (comparison ? (
          <StrategyComparison comparison={comparison} />
        ) : (
          <Panel title="Strategy comparison">
            <p className="py-6 text-center text-sm text-muted">
              No results yet.
            </p>
          </Panel>
        ))}

      {tab === "agents" && (
        <AgentPerformanceChart performances={performances} shockSteps={shockSteps} />
      )}

      {tab === "book" && (
        <OrderBookView
          snapshot={book}
          step={bookStep}
          maxStep={sim.duration_steps}
          onStepChange={setBookStep}
        />
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-muted">{label}</div>
      <div className="num mt-0.5 text-base text-slate-100">{value}</div>
    </div>
  );
}
