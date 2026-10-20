import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError, optional } from "../api/client";
import type {
  AgentPerformance,
  Comparison,
  GreeksAttribution,
  LatencyRaces,
  LiquiditySurface,
  Microprice,
  Microstructure,
  OrderBookSnapshot,
  Simulation,
} from "../api/types";
import type { DataDep, ModuleData } from "../modules/types";

const DEPS = [
  "simulation",
  "comparison",
  "performance",
  "book",
  "surface",
  "microstructure",
  "latency",
  "greeks",
  "microprice",
] as const satisfies readonly DataDep[];

/** Everything downstream of the run itself, settled together when a run is incomplete. */
const DERIVED = DEPS.filter((dep) => dep !== "simulation");

const allLoading = (value: boolean) =>
  Object.fromEntries(DEPS.map((dep) => [dep, value])) as Record<DataDep, boolean>;

/** The shell owns every fetch. */
export function useSimulationData(id: string): ModuleData {
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [performances, setPerformances] = useState<AgentPerformance[]>([]);
  const [book, setBook] = useState<OrderBookSnapshot | null>(null);
  const [surface, setSurface] = useState<LiquiditySurface | null>(null);
  const [microstructure, setMicrostructure] = useState<Microstructure | null>(null);
  const [latency, setLatency] = useState<LatencyRaces | null>(null);
  const [greeks, setGreeks] = useState<GreeksAttribution | null>(null);
  const [microprice, setMicroprice] = useState<Microprice | null>(null);
  const [step, setStep] = useState(0);
  const [nonce, setNonce] = useState(0);

  const [loading, setLoading] = useState<Record<DataDep, boolean>>(() => allLoading(true));
  const [errors, setErrors] = useState<Partial<Record<DataDep, string>>>({});

  const mark = useCallback((dep: DataDep, value: boolean) => {
    setLoading((prev) => ({ ...prev, [dep]: value }));
  }, []);
  const fail = useCallback((dep: DataDep, error: unknown) => {
    setErrors((prev) => ({
      ...prev,
      [dep]: error instanceof ApiError ? error.message : String(error),
    }));
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      // A reload is a fresh attempt at everything, so last attempt's errors go first.
      // Without this a Retry that succeeds still leaves the panel showing the old failure.
      setErrors({});
      setLoading(allLoading(true));
      try {
        const sim = await api.getSimulation(id);
        if (cancelled) return;
        setSimulation(sim);
        setStep((current) => current || sim.duration_steps);
        mark("simulation", false);

        if (sim.status !== "COMPLETED") {
          // Nothing downstream exists yet; say so rather than leaving every panel spinning forever.
          DERIVED.forEach((dep) => mark(dep, false));
          return;
        }

        // Each dependency: settle it, record a named error if it fails, clear its spinner.
        const run = <T,>(dep: DataDep, promise: Promise<T>, apply: (value: T) => void) => {
          void promise
            .then((value) => !cancelled && apply(value))
            .catch((error) => !cancelled && fail(dep, error))
            .finally(() => !cancelled && mark(dep, false));
        };

        run("comparison", api.comparison(id), setComparison);
        run(
          "performance",
          Promise.all(sim.agents.map((a) => api.agentPerformance(id, a.id))),
          setPerformances,
        );
        run("surface", api.liquiditySurface(id, 40), setSurface);
        run("microstructure", api.microstructure(id), setMicrostructure);
        run("latency", api.latencyRaces(id), setLatency);
        // A run with no options maker has nothing to attribute, and the API says so with a 409.
        run("greeks", optional(api.greeksAttribution(id)), setGreeks);
        run("microprice", api.microprice(id, 2), setMicroprice);
      } catch (error) {
        if (!cancelled) {
          fail("simulation", error);
          setLoading(allLoading(false));
        }
      }
    }

    void load();
    return () => {
      cancelled = true;
    };
  }, [id, nonce, mark, fail]);

  // The book is the one dependency that changes with the scrubber, so it is fetched on its own.
  useEffect(() => {
    if (!simulation || simulation.status !== "COMPLETED" || !step) return;
    let cancelled = false;
    mark("book", true);
    setErrors((prev) => ({ ...prev, book: undefined }));
    api
      .orderBook(id, step, 12)
      .then((snapshot) => !cancelled && setBook(snapshot))
      .catch((error) => !cancelled && fail("book", error))
      .finally(() => !cancelled && mark("book", false));
    return () => {
      cancelled = true;
    };
  }, [id, step, simulation, mark, fail]);

  const endpoints = useMemo(
    () => ({
      simulation: `GET /api/simulations/${id}`,
      comparison: `GET /api/simulations/${id}/comparison`,
      performance: `GET /api/simulations/${id}/agents/{agent}/performance`,
      book: `GET /api/simulations/${id}/order-book/snapshot?step=${step}`,
      surface: `GET /api/simulations/${id}/liquidity-surface?levels=40`,
      microstructure: `GET /api/simulations/${id}/microstructure`,
      latency: `GET /api/simulations/${id}/latency-races`,
      greeks: `GET /api/simulations/${id}/greeks/attribution`,
      microprice: `GET /api/simulations/${id}/microprice?stride=2`,
    }),
    [id, step],
  );

  return {
    simulation,
    comparison,
    performances,
    book,
    surface,
    microstructure,
    latency,
    greeks,
    microprice,
    step,
    setStep,
    loading,
    errors,
    endpoints,
    reload: () => setNonce((n) => n + 1),
  };
}
