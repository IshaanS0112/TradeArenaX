import { lazy } from "react";

import type { ModuleDefinition, ModuleWidth } from "./types";
import { BookHeatmap } from "./BookHeatmap";
import { BookLadder } from "./BookLadder";
import { DepthChart } from "./DepthChart";
import { AgentMatrix, InventoryCurve, MarketChart, PnLCurve } from "./PerformanceModules";
import {
  GreeksAttribution,
  LatencyRaceModule,
  MicropriceModule,
  SpreadDecomposition,
} from "./MicrostructureModules";
import { ConservationCheck, MarketTape } from "./RiskModules";

// three.js is ~600 KB parsed and only one module needs it, so it is loaded on demand.
const LiquiditySurface3D = lazy(() => import("./LiquiditySurface3D"));

export const MODULES: ModuleDefinition[] = [
  {
    id: "book.ladder",
    title: "Book ladder",
    subtitle: "Resting liquidity either side of the touch, at the scrubbed step",
    category: "book",
    dataDeps: ["book"],
    defaultWidth: 1,
    defaultHeight: 340,
    Component: BookLadder,
  },
  {
    id: "book.depth",
    title: "Depth curve",
    subtitle: "Cumulative quantity — how far a market order would walk the book",
    category: "book",
    dataDeps: ["book"],
    defaultWidth: 1,
    defaultHeight: 340,
    Component: DepthChart,
  },
  {
    id: "book.heatmap",
    title: "Liquidity heatmap",
    subtitle: "Depth over time, price relative to mid — the shock as a visible tear",
    category: "book",
    dataDeps: ["surface"],
    defaultWidth: 2,
    defaultHeight: 340,
    Component: BookHeatmap,
  },
  {
    id: "book.surface3d",
    title: "Liquidity surface (3D)",
    subtitle: "The same data as terrain: price × time × resting quantity",
    category: "book",
    dataDeps: ["surface"],
    // Full width: the hero panel of the Microstructure preset, and a terrain view foreshortens badly.
    defaultWidth: 3,
    defaultHeight: 460,
    lazy: true,
    Component: LiquiditySurface3D,
  },
  {
    id: "market.price",
    title: "Reference vs mid",
    subtitle: "The synthetic path and the book's own mid, with the shock marked",
    category: "performance",
    dataDeps: ["comparison"],
    defaultWidth: 2,
    defaultHeight: 320,
    Component: MarketChart,
  },
  {
    id: "perf.pnl",
    title: "PnL curves",
    subtitle: "Total PnL per agent — realised plus mark-to-market",
    category: "performance",
    dataDeps: ["performance", "comparison"],
    defaultWidth: 2,
    defaultHeight: 320,
    Component: PnLCurve,
  },
  {
    id: "perf.inventory",
    title: "Inventory",
    subtitle: "Signed position per agent: the risk a maker takes to earn the spread",
    category: "risk",
    dataDeps: ["performance", "comparison"],
    defaultWidth: 2,
    defaultHeight: 320,
    Component: InventoryCurve,
  },
  {
    id: "perf.matrix",
    title: "Agent matrix",
    subtitle: "Every metric side by side; a dash where the data cannot support one",
    category: "performance",
    dataDeps: ["comparison"],
    defaultWidth: 2,
    defaultHeight: 340,
    Component: AgentMatrix,
  },
  {
    id: "risk.spread",
    title: "Spread decomposition",
    subtitle: "What the maker earned, and what informed flow took back",
    category: "risk",
    dataDeps: ["microstructure"],
    defaultWidth: 2,
    defaultHeight: 340,
    Component: SpreadDecomposition,
  },
  {
    id: "diag.latency",
    title: "Latency race",
    subtitle: "One contested fill: decided, cancel sent, hit, cancel arrives",
    category: "diagnostics",
    dataDeps: ["latency"],
    defaultWidth: 1,
    defaultHeight: 340,
    Component: LatencyRaceModule,
  },
  {
    id: "deriv.greeks",
    title: "Greeks attribution",
    subtitle: "Gamma, theta and hedging slippage on a delta-hedged book",
    category: "derivatives",
    dataDeps: ["greeks"],
    defaultWidth: 2,
    defaultHeight: 340,
    Component: GreeksAttribution,
  },
  {
    id: "book.microprice",
    title: "Microprice vs mid",
    subtitle: "Imbalance-weighted fair value, and whether it actually forecasts",
    category: "book",
    dataDeps: ["microprice"],
    defaultWidth: 2,
    defaultHeight: 340,
    Component: MicropriceModule,
  },
  {
    id: "risk.conservation",
    title: "Conservation check",
    subtitle: "Σ agent PnL must be zero — shown, not claimed",
    category: "risk",
    dataDeps: ["comparison", "simulation"],
    defaultWidth: 1,
    defaultHeight: 340,
    Component: ConservationCheck,
  },
  {
    id: "diag.tape",
    title: "Step tape",
    subtitle: "Every step: reference, mid, spread, σ, trades. Click to scrub",
    category: "diagnostics",
    dataDeps: ["comparison"],
    defaultWidth: 1,
    defaultHeight: 340,
    Component: MarketTape,
  },
];

export const MODULE_BY_ID = new Map(MODULES.map((m) => [m.id, m]));

/** Named layouts. */
interface PresetEntry {
  id: string;
  /** Overrides the module's own default width. */
  width?: ModuleWidth;
}

interface Preset {
  id: string;
  label: string;
  modules: PresetEntry[];
}

const MICROSTRUCTURE: Preset = {
  id: "microstructure",
  label: "Microstructure",
  modules: [
    { id: "book.surface3d", width: 3 },
    { id: "book.ladder", width: 1 },
    { id: "book.heatmap", width: 2 },
    { id: "book.microprice", width: 2 },
    { id: "diag.tape", width: 1 },
    { id: "book.depth", width: 3 },
  ],
};

/** Named layouts. */
export const PRESETS: Preset[] = [
  MICROSTRUCTURE,
  {
    id: "performance",
    label: "Performance",
    modules: [
      { id: "perf.pnl", width: 2 },
      { id: "risk.conservation", width: 1 },
      { id: "market.price", width: 2 },
      { id: "diag.tape", width: 1 },
      { id: "perf.matrix", width: 3 },
    ],
  },
  {
    id: "risk",
    label: "Risk",
    modules: [
      { id: "risk.spread", width: 2 },
      { id: "diag.latency", width: 1 },
      { id: "perf.inventory", width: 2 },
      { id: "risk.conservation", width: 1 },
      { id: "perf.matrix", width: 3 },
    ],
  },
  {
    id: "derivatives",
    label: "Derivatives",
    modules: [
      { id: "deriv.greeks", width: 2 },
      { id: "risk.conservation", width: 1 },
      { id: "book.microprice", width: 2 },
      { id: "diag.latency", width: 1 },
      { id: "perf.matrix", width: 3 },
    ],
  },
];

export const DEFAULT_PRESET = MICROSTRUCTURE;
