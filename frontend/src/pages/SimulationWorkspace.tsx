import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useSimulationData } from "../data/useSimulationData";
import { Button, Stat, Tag } from "../design/primitives";
import { ModuleFrame } from "../layout/ModuleFrame";
import { DEFAULT_PRESET, MODULES, MODULE_BY_ID, PRESETS } from "../modules/registry";
import type { ModuleWidth } from "../modules/types";
import { num } from "../components/format";

interface Placement {
  id: string;
  width: ModuleWidth;
}

function presetPlacements(presetId: string): Placement[] {
  const preset = PRESETS.find((p) => p.id === presetId) ?? DEFAULT_PRESET;
  return preset.modules
    .map((entry) => {
      const definition = MODULE_BY_ID.get(entry.id);
      if (!definition) return null;
      return { id: definition.id, width: entry.width ?? definition.defaultWidth };
    })
    .filter((placement): placement is Placement => placement !== null);
}

/** Layout lives in localStorage, per simulation. */
function loadLayout(simulationId: string): { preset: string; placements: Placement[] } | null {
  try {
    const raw = window.localStorage.getItem(`tax.layout.${simulationId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { preset: string; placements: Placement[] };
    if (!Array.isArray(parsed.placements)) return null;
    // Drop ids that no longer exist so a removed module cannot break the page.
    parsed.placements = parsed.placements.filter((p) => MODULE_BY_ID.has(p.id));
    return parsed;
  } catch {
    return null;
  }
}

export default function SimulationWorkspace() {
  const { id = "" } = useParams();
  const data = useSimulationData(id);
  // The preset lives in the URL as well as in storage, so a layout can be sent to someone: "look.
  const [params, setParams] = useSearchParams();

  const [preset, setPreset] = useState(DEFAULT_PRESET.id);
  const [placements, setPlacements] = useState<Placement[]>(() =>
    presetPlacements(DEFAULT_PRESET.id),
  );
  const [pickerOpen, setPickerOpen] = useState(false);
  const stepFromUrl = useRef(false);

  // ?step= parks the scrubber somewhere specific, so "the book at the shock" is a link rather.
  useEffect(() => {
    if (stepFromUrl.current || !data.simulation) return;
    const requested = Number(params.get("step"));
    if (Number.isFinite(requested) && requested >= 1) {
      data.setStep(Math.min(requested, data.simulation.duration_steps));
    }
    stepFromUrl.current = true;
  }, [params, data]);

  useEffect(() => {
    const requested = params.get("preset");
    if (requested && PRESETS.some((p) => p.id === requested)) {
      setPreset(requested);
      setPlacements(presetPlacements(requested));
      return;
    }
    const stored = loadLayout(id);
    if (stored) {
      setPreset(stored.preset);
      setPlacements(stored.placements);
    } else {
      setPreset(DEFAULT_PRESET.id);
      setPlacements(presetPlacements(DEFAULT_PRESET.id));
    }
    // `params` deliberately excluded: this decides the initial layout for a simulation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    try {
      window.localStorage.setItem(
        `tax.layout.${id}`,
        JSON.stringify({ preset, placements }),
      );
    } catch {
      // A browser with storage disabled still gets a working dashboard; it just forgets the layout.
    }
  }, [id, preset, placements]);

  const applyPreset = useCallback(
    (presetId: string) => {
      setPreset(presetId);
      setPlacements(presetPlacements(presetId));
      setParams({ preset: presetId }, { replace: true });
    },
    [setParams],
  );

  const move = useCallback((index: number, delta: -1 | 1) => {
    setPlacements((current) => {
      const next = [...current];
      const target = index + delta;
      const a = next[index];
      const b = next[target];
      if (!a || !b) return current;
      next[index] = b;
      next[target] = a;
      return next;
    });
  }, []);

  const simulation = data.simulation;
  const summary =
    simulation && "total_trades" in simulation.run_summary ? simulation.run_summary : null;
  const shocks = simulation?.volatility_shock_config.shocks ?? [];
  const available = useMemo(
    () => MODULES.filter((m) => !placements.some((p) => p.id === m.id)),
    [placements],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0">
          <Link to="/" className="text-2xs text-muted hover:text-accent">
            ← All simulations
          </Link>
          <h1 className="mt-1 truncate text-xl font-semibold tracking-tight text-primary">
            {simulation?.name ?? "Loading…"}
          </h1>
          {simulation && (
            <p className="num mt-1 text-2xs text-muted">
              seed {simulation.price_process_config.random_seed} · σ{" "}
              {num(simulation.price_process_config.volatility, 2)} · drift{" "}
              {num(simulation.price_process_config.drift, 2)} · S₀{" "}
              {num(simulation.price_process_config.initial_price, 2)} ·{" "}
              {simulation.duration_steps} steps · {simulation.agents.length} agents
              {shocks.length > 0 &&
                ` · shock at ${shocks.map((s) => s.step).join(", ")}`}
            </p>
          )}
        </div>
        {simulation && (
          <div className="ml-auto flex items-center gap-2">
            <Tag tone={simulation.status === "COMPLETED" ? "bid" : "warn"}>
              {simulation.status}
            </Tag>
            <Button onClick={data.reload}>Reload</Button>
          </div>
        )}
      </div>

      {summary && (
        <div className="grid grid-cols-2 gap-4 rounded-module border border-edge bg-panel px-4 py-3 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Steps" value={num(summary.steps_run, 0)} />
          <Stat label="Trades" value={num(summary.total_trades, 0)} />
          <Stat label="Orders" value={num(summary.total_orders, 0)} />
          <Stat label="σ configured" value={num(summary.configured_volatility, 3)} />
          <Stat label="σ realised" value={num(summary.realized_volatility, 3)} />
          <Stat
            label="Final mid"
            value={num(summary.final_mid_price, 2)}
            hint={`reference ${num(summary.final_reference_price, 2)}`}
          />
        </div>
      )}

      {/* Controls: preset, module picker, and the shared step scrubber. Every
          module that shows a point in time reads the same step, so the ladder
          and the surface plane can never disagree. */}
      <div className="flex flex-wrap items-center gap-3 rounded-module border border-edge bg-panel px-3 py-2">
        <div className="flex items-center gap-1">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => applyPreset(p.id)}
              className={`rounded-input px-2 py-1 text-xs transition-colors duration-fast ${
                preset === p.id
                  ? "bg-accent/15 text-accent"
                  : "text-secondary hover:text-primary"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div className="relative">
          <Button onClick={() => setPickerOpen((open) => !open)} disabled={!available.length}>
            + Module
          </Button>
          {pickerOpen && available.length > 0 && (
            <div className="absolute z-20 mt-1 w-72 rounded-module border border-edge bg-raised p-1 shadow-xl">
              {available.map((module) => (
                <button
                  key={module.id}
                  type="button"
                  onClick={() => {
                    setPlacements((current) => [
                      ...current,
                      { id: module.id, width: module.defaultWidth },
                    ]);
                    setPickerOpen(false);
                  }}
                  className="block w-full rounded-input px-2 py-1.5 text-left hover:bg-panel"
                >
                  <span className="text-xs text-primary">{module.title}</span>
                  <span className="block truncate text-2xs text-muted">{module.subtitle}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <Button variant="quiet" onClick={() => applyPreset(preset)}>
          Reset layout
        </Button>

        {simulation && simulation.duration_steps > 0 && (
          <label className="ml-auto flex min-w-[260px] flex-1 items-center gap-2 text-2xs text-muted">
            <span className="uppercase tracking-wide">Step</span>
            <input
              type="range"
              min={1}
              max={simulation.duration_steps}
              value={data.step || simulation.duration_steps}
              onChange={(event) => data.setStep(Number(event.target.value))}
              className="h-1 flex-1 accent-[color:var(--accent)]"
              aria-label="Scrub the book to a step"
            />
            <span className="num w-12 text-right text-primary">{data.step}</span>
          </label>
        )}
      </div>

      {simulation && simulation.status !== "COMPLETED" && (
        <p className="rounded-module border border-warn/40 bg-warn/10 p-3 text-xs text-warn">
          This simulation has not been run yet, so every module below is empty.
        </p>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        {placements.map((placement, index) => {
          const definition = MODULE_BY_ID.get(placement.id);
          if (!definition) return null;
          const { Component } = definition;
          return (
            <ModuleFrame
              key={placement.id}
              definition={definition}
              width={placement.width}
              onWidth={(width) =>
                setPlacements((current) =>
                  current.map((p, i) => (i === index ? { ...p, width } : p)),
                )
              }
              onMove={(delta) => move(index, delta)}
              onRemove={() =>
                setPlacements((current) => current.filter((_, i) => i !== index))
              }
            >
              <Component data={data} />
            </ModuleFrame>
          );
        })}
      </div>

      {placements.length === 0 && (
        <p className="rounded-module border border-edge bg-panel p-6 text-center text-xs text-muted">
          No modules. Add one, or pick a preset.
        </p>
      )}
    </div>
  );
}
