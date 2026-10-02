import type {
  Agent,
  AgentDefaults,
  AgentPerformance,
  AgentType,
  Comparison,
  GreeksAttribution,
  LatencyRaces,
  LiquiditySurface,
  Microprice,
  Microstructure,
  OrderBookSnapshot,
  RunSummary,
  Simulation,
} from "./types";

// In dev, Vite proxies /api -> :8000.
const BASE = import.meta.env.VITE_API_BASE_URL || "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = await response.text();
    }
    // FastAPI's detail is a string for our explicit HTTPExceptions and an array of error objects.
    const message =
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail
              .map((d: { loc?: string[]; msg?: string }) =>
                `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`,
              )
              .join("; ")
          : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }

  if (response.status === 204) return undefined as T;

  // A 200 that is not JSON means the request never reached the API.
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    throw new ApiError(
      `Expected JSON from ${BASE}${path} but received "${contentType || "no content-type"}". ` +
        "The request was probably routed to the frontend instead of the API - " +
        "check VITE_API_BASE_URL and the /api proxy in nginx.conf or vite.config.ts.",
      response.status,
    );
  }

  return (await response.json()) as T;
}

/** A 409 from a simulation that has not been run yet is expected, not an error. */
export async function optional<T>(promise: Promise<T>): Promise<T | null> {
  try {
    return await promise;
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 409)) {
      return null;
    }
    throw error;
  }
}

export interface SimulationDraft {
  name: string;
  price_process: {
    initial_price: number;
    drift: number;
    volatility: number;
    random_seed: number;
  };
  shocks: {
    step: number;
    magnitude_pct: number;
    vol_multiplier: number;
    vol_half_life_steps: number;
  }[];
}

export const api = {
  agentDefaults: () => request<AgentDefaults>("/meta/agent-defaults"),
  engineConfig: () => request<Record<string, unknown>>("/meta/engine-config"),

  listSimulations: () => request<Simulation[]>("/simulations"),
  getSimulation: (id: string) => request<Simulation>(`/simulations/${id}`),
  createSimulation: (body: SimulationDraft) =>
    request<Simulation>("/simulations", { method: "POST", body: JSON.stringify(body) }),
  deleteSimulation: (id: string) =>
    request<void>(`/simulations/${id}`, { method: "DELETE" }),

  addAgent: (id: string, agent_type: AgentType, config: Record<string, unknown>) =>
    request<Agent>(`/simulations/${id}/agents`, {
      method: "POST",
      body: JSON.stringify({ agent_type, config }),
    }),

  run: (id: string, steps: number, persist_every_n_steps = 1) =>
    request<RunSummary>(`/simulations/${id}/run`, {
      method: "POST",
      body: JSON.stringify({ steps, persist_every_n_steps }),
    }),

  orderBook: (id: string, step?: number, levels = 12) => {
    const params = new URLSearchParams({ levels: String(levels) });
    if (step !== undefined) params.set("step", String(step));
    return request<OrderBookSnapshot>(
      `/simulations/${id}/order-book/snapshot?${params.toString()}`,
    );
  },

  agentPerformance: (id: string, agentId: string) =>
    request<AgentPerformance>(`/simulations/${id}/agents/${agentId}/performance`),

  comparison: (id: string) => request<Comparison>(`/simulations/${id}/comparison`),

  microstructure: (id: string) =>
    request<Microstructure>(`/simulations/${id}/microstructure`),

  latencyRaces: (id: string) => request<LatencyRaces>(`/simulations/${id}/latency-races`),

  greeksAttribution: (id: string) =>
    request<GreeksAttribution>(`/simulations/${id}/greeks/attribution`),

  microprice: (id: string, stride = 1) =>
    request<Microprice>(`/simulations/${id}/microprice?stride=${stride}`),

  liquiditySurface: (id: string, levels = 40, stride = 0) =>
    request<LiquiditySurface>(
      `/simulations/${id}/liquidity-surface?levels=${levels}&stride=${stride}`,
    ),
};
