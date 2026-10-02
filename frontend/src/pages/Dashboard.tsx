import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { Simulation } from "../api/types";
import { Empty, Panel } from "../components/Panel";
import { num } from "../components/format";

export default function Dashboard() {
  const [simulations, setSimulations] = useState<Simulation[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setSimulations(await api.listSimulations());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not reach the API.");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function remove(id: string) {
    await api.deleteSimulation(id);
    void load();
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-primary">
            Simulations
          </h1>
          <p className="mt-1 text-sm text-muted">
            Each run is a synthetic price path, a limit order book, and a set of
            competing agents. Same seed and config, same result.
          </p>
        </div>
        <Link
          to="/new"
          className="ml-auto rounded bg-accent px-4 py-2 text-sm font-medium text-base hover:bg-accent/90"
        >
          New simulation
        </Link>
      </div>

      {error && (
        <p className="rounded border border-negative/40 bg-negative/10 p-3 text-sm text-negative">
          {error}
        </p>
      )}

      <Panel title="Runs">
        {simulations === null ? (
          <Empty>Loading…</Empty>
        ) : simulations.length === 0 ? (
          <Empty>
            No simulations yet. Create one to see a book, three agents, and a
            comparison table.
          </Empty>
        ) : (
          <ul className="divide-y divide-edge">
            {simulations.map((sim) => {
              const shocks = sim.volatility_shock_config.shocks ?? [];
              const summary =
                "total_trades" in sim.run_summary ? sim.run_summary : null;
              return (
                <li key={sim.id} className="flex flex-wrap items-center gap-4 py-3">
                  <div className="min-w-0 flex-1">
                    <Link
                      to={`/simulations/${sim.id}`}
                      className="text-sm font-medium text-primary hover:text-accent"
                    >
                      {sim.name}
                    </Link>
                    <p className="mt-0.5 text-xs text-muted">
                      seed {sim.price_process_config.random_seed} · sigma{" "}
                      {num(sim.price_process_config.volatility, 2)} · drift{" "}
                      {num(sim.price_process_config.drift, 2)} ·{" "}
                      {sim.agents.length} agents
                      {shocks.length > 0 &&
                        ` · shock${shocks.length > 1 ? "s" : ""} at step ${shocks
                          .map((s) => s.step)
                          .join(", ")}`}
                    </p>
                  </div>

                  <span
                    className={`rounded px-2 py-0.5 text-[11px] uppercase tracking-wide ${
                      sim.status === "COMPLETED"
                        ? "bg-positive/15 text-positive"
                        : "bg-edge text-muted"
                    }`}
                  >
                    {sim.status}
                  </span>

                  {summary && (
                    <span className="num text-xs text-muted">
                      {summary.steps_run} steps · {summary.total_trades} trades
                    </span>
                  )}

                  <button
                    onClick={() => void remove(sim.id)}
                    className="text-xs text-muted hover:text-negative"
                  >
                    Delete
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}
