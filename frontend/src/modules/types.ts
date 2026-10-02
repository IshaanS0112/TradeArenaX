import type { FC } from "react";

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

/** What a module can ask the shell. */
export type DataDep =
  | "simulation"
  | "comparison"
  | "performance"
  | "book"
  | "surface"
  | "microstructure"
  | "latency"
  | "greeks"
  | "microprice";

export interface ModuleData {
  simulation: Simulation | null;
  comparison: Comparison | null;
  performances: AgentPerformance[];
  book: OrderBookSnapshot | null;
  surface: LiquiditySurface | null;
  microstructure: Microstructure | null;
  latency: LatencyRaces | null;
  greeks: GreeksAttribution | null;
  microprice: Microprice | null;
  /** The step the book scrubber is parked on, shared by every module that shows a point in time. */
  step: number;
  setStep: (step: number) => void;
  /** Per-dependency load and error state, so a module renders its own skeleton or a named error. */
  loading: Record<DataDep, boolean>;
  errors: Partial<Record<DataDep, string>>;
  endpoints: Record<DataDep, string>;
}

export type ModuleCategory =
  | "book"
  | "performance"
  | "risk"
  | "derivatives"
  | "diagnostics";

export type ModuleWidth = 1 | 2 | 3;

export interface ModuleDefinition {
  id: string;
  title: string;
  /** One line under the title. */
  subtitle: string;
  category: ModuleCategory;
  dataDeps: readonly DataDep[];
  defaultWidth: ModuleWidth;
  /** Fixed body height in px. */
  defaultHeight: number;
  /** 3D modules are lazy so three.js never enters the main bundle. */
  lazy?: boolean;
  Component: FC<{ data: ModuleData }>;
}
