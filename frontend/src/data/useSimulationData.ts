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

/** The shell owns every fetch. */
export function useSimulationData(id: string): ModuleData & { reload: () => void } {
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

  const [loading, setLoading] = useState<Record<DataDep, boolean>>({
    simulation: true,
    comparison: true,
    performance: true,
    book: true,
    surface: true,
    microstructure: true,
    latency: true,
    greeks: true,
    microprice: true,
  });
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
      try {
        const sim = await api.getSimulation(id);
        if (cancelled) return;
        setSimulation(sim);
        setStep((current) => current || sim.duration_steps);
        mark("simulation", false);

        if (sim.status !== "COMPLETED") {
          // Nothing downstream exists yet; say so rather than leaving every panel spinning forever.
          (
            [
              "comparison",
              "performance",
              "book",
              "surface",
              "microstructure",
              "latency",
              "greeks",
              "microprice",
            ] as DataDep[]
          ).forEach((dep) => mark(dep, false));
          return;
        }

        void api
          .comparison(id)
          .then((c) => !cancelled && setComparison(c))
          .catch((e) => !cancelled && fail("comparison", e))
          .finally(() => !cancelled && mark("comparison", false));

        void Promise.all(sim.agents.map((a) => api.agentPerformance(id, a.id)))
          .then((p) => !cancelled && setPerformances(p))
          .catch((e) => !cancelled && fail("performance", e))
          .finally(() => !cancelled && mark("performance", false));

        void api
          .liquiditySurface(id, 40)
          .then((s) => !cancelled && setSurface(s))
          .catch((e) => !cancelled && fail("surface", e))
          .finally(() => !cancelled && mark("surface", false));

        void api
          .microstructure(id)
          .then((m) => !cancelled && setMicrostructure(m))
          .catch((e) => !cancelled && fail("microstructure", e))
          .finally(() => !cancelled && mark("microstructure", false));

        void api
          .latencyRaces(id)
          .then((l) => !cancelled && setLatency(l))
          .catch((e) => !cancelled && fail("latency", e))
          .finally(() => !cancelled && mark("latency", false));

        // A run with no options maker has nothing to attribute, and the API says so with a 409.
        void optional(api.greeksAttribution(id))
          .then((g) => !cancelled && setGreeks(g))
          .catch((e) => !cancelled && fail("greeks", e))
          .finally(() => !cancelled && mark("greeks", false));

        void api
          .microprice(id, 2)
          .then((m) => !cancelled && setMicroprice(m))
          .catch((e) => !cancelled && fail("microprice", e))
          .finally(() => !cancelled && mark("microprice", false));
      } catch (error) {
        if (!cancelled) {
          fail("simulation", error);
          (
            [
              "simulation",
              "comparison",
              "performance",
              "book",
              "surface",
              "microstructure",
              "latency",
              "greeks",
              "microprice",
            ] as DataDep[]
          ).forEach((dep) => mark(dep, false));
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
