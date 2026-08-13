import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { AgentDefaults, AgentType } from "../api/types";
import { Panel } from "../components/Panel";
import { AGENT_LABEL } from "../components/format";

const ALL_TYPES: AgentType[] = ["MARKET_MAKER", "MOMENTUM", "MEAN_REVERSION"];

export default function NewSimulation() {
  const navigate = useNavigate();
  const [defaults, setDefaults] = useState<AgentDefaults | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("Baseline: 3 agents, mid-run shock");
  const [initialPrice, setInitialPrice] = useState(100);
  const [drift, setDrift] = useState(0);
  const [volatility, setVolatility] = useState(0.3);
  const [seed, setSeed] = useState(42);
  const [steps, setSteps] = useState(1000);

  const [shockEnabled, setShockEnabled] = useState(true);
  const [shockStep, setShockStep] = useState(500);
  const [shockPct, setShockPct] = useState(-8);
  const [volMultiplier, setVolMultiplier] = useState(3);
  const [halfLife, setHalfLife] = useState(60);

  const [selected, setSelected] = useState<Set<AgentType>>(new Set(ALL_TYPES));

  useEffect(() => {
    // Agent parameter defaults come from the API so this form cannot drift out of
    // sync with the engine's actual tunables.
    void api.agentDefaults().then(setDefaults).catch(() => setDefaults(null));
  }, []);

  const shockPastEnd = shockEnabled && shockStep > steps;
  const noMaker = !selected.has("MARKET_MAKER");

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const sim = await api.createSimulation({
        name,
        price_process: {
          initial_price: initialPrice,
          drift,
          volatility,
          random_seed: seed,
        },
        shocks: shockEnabled
          ? [
              {
                step: shockStep,
                magnitude_pct: shockPct,
                vol_multiplier: volMultiplier,
                vol_half_life_steps: halfLife,
              },
            ]
          : [],
      });

      for (const type of ALL_TYPES.filter((t) => selected.has(t))) {
        await api.addAgent(sim.id, type, {});
      }

      await api.run(sim.id, steps, steps > 2000 ? 5 : 1);
      navigate(`/simulations/${sim.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100">
          New simulation
        </h1>
        <p className="mt-1 text-sm text-muted">
          Volatility and drift are annualised. One step is nominally one minute of
          a trading session, so a 1,000-step run is about two and a half hours of
          market time.
        </p>
      </div>

      {error && (
        <p className="rounded border border-negative/40 bg-negative/10 p-3 text-sm text-negative">
          {error}
        </p>
      )}

      <Panel title="Run">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Name" className="sm:col-span-2 lg:col-span-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              maxLength={200}
              className={inputClass}
            />
          </Field>
          <Number label="Steps" value={steps} onChange={setSteps} min={1} max={20000} />
          <Number label="Initial price" value={initialPrice} onChange={setInitialPrice} min={0.01} step={0.01} />
          <Number label="Random seed" value={seed} onChange={setSeed} step={1} />
          <Number label="Drift (mu, annual)" value={drift} onChange={setDrift} step={0.05} />
          <Number
            label="Volatility (sigma, annual)"
            value={volatility}
            onChange={setVolatility}
            min={0}
            max={10}
            step={0.05}
          />
        </div>
      </Panel>

      <Panel
        title="Volatility shock"
        subtitle="A jump discontinuity applied on top of that step's diffusion, optionally leaving elevated volatility that decays with a half-life."
      >
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={shockEnabled}
            onChange={(e) => setShockEnabled(e.target.checked)}
            className="accent-accent"
          />
          Inject a shock
        </label>

        {shockEnabled && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Number label="At step" value={shockStep} onChange={setShockStep} min={1} />
            <Number
              label="Jump (%)"
              value={shockPct}
              onChange={setShockPct}
              min={-99}
              step={0.5}
            />
            <Number
              label="Vol multiplier"
              value={volMultiplier}
              onChange={setVolMultiplier}
              min={1}
              step={0.5}
            />
            <Number
              label="Vol half-life (steps)"
              value={halfLife}
              onChange={setHalfLife}
              min={1}
            />
          </div>
        )}

        {shockPastEnd && (
          <p className="mt-3 text-xs text-caution">
            Step {shockStep} is past the end of a {steps}-step run, so the shock
            would never fire. The API rejects this.
          </p>
        )}
      </Panel>

      <Panel title="Agents" subtitle="Each agent runs with its default parameter set.">
        <div className="grid gap-3 sm:grid-cols-3">
          {ALL_TYPES.map((type) => (
            <label
              key={type}
              className={`cursor-pointer rounded border p-3 text-sm ${
                selected.has(type)
                  ? "border-accent/60 bg-accent/5"
                  : "border-edge hover:border-edge/80"
              }`}
            >
              <span className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={selected.has(type)}
                  onChange={(e) => {
                    const next = new Set(selected);
                    if (e.target.checked) next.add(type);
                    else next.delete(type);
                    setSelected(next);
                  }}
                  className="accent-accent"
                />
                {AGENT_LABEL[type]}
              </span>
              {defaults?.[type] && (
                <dl className="mt-2 space-y-0.5 text-[11px] text-muted">
                  {Object.entries(defaults[type])
                    .slice(0, 5)
                    .map(([key, value]) => (
                      <div key={key} className="flex justify-between gap-2">
                        <dt className="truncate">{key}</dt>
                        <dd className="num">{String(value)}</dd>
                      </div>
                    ))}
                </dl>
              )}
            </label>
          ))}
        </div>

        {noMaker && (
          <p className="mt-3 text-xs text-caution">
            Without a market maker nothing provides liquidity: the directional
            agents can only take it, so the book stays empty and almost nothing
            trades.
          </p>
        )}
      </Panel>

      <button
        type="submit"
        disabled={busy || shockPastEnd || selected.size === 0}
        className="rounded bg-accent px-5 py-2 text-sm font-medium text-ink hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-40"
      >
        {busy ? "Running…" : "Create and run"}
      </button>
    </form>
  );
}

const inputClass =
  "w-full rounded border border-edge bg-ink px-2 py-1.5 text-sm text-slate-100 focus:border-accent focus:outline-none";

function Field({
  label,
  children,
  className = "",
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={`block ${className}`}>
      <span className="mb-1 block text-xs text-muted">{label}</span>
      {children}
    </label>
  );
}

function Number({
  label,
  value,
  onChange,
  ...rest
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
}) {
  return (
    <Field label={label}>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.valueAsNumber)}
        className={`${inputClass} num`}
        {...rest}
      />
    </Field>
  );
}
