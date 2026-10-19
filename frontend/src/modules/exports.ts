/**
 * CSV shapes, one per panel that has a tabular source.
 *
 * Kept beside the registry rather than inside each module: a module renders, and what a
 * reader wants in a spreadsheet is rarely the same shape as what is on screen. The book
 * ladder draws two stacks around the touch; the export is one column of signed levels.
 */

import type { CsvTable } from "../components/csv";
import type { ModuleData } from "./types";

export function bookLadderRows(data: ModuleData): CsvTable | null {
  const book = data.book;
  if (!book) return null;
  const rows = [
    ...book.asks.map((l) => ["ask", l.price, l.quantity, l.cumulative_quantity, l.order_count]),
    ...book.bids.map((l) => ["bid", l.price, l.quantity, l.cumulative_quantity, l.order_count]),
  ];
  return {
    name: `book-step-${data.step}`,
    columns: ["side", "price", "quantity", "cumulative_quantity", "order_count"],
    rows,
  };
}

export function marketTapeRows(data: ModuleData): CsvTable | null {
  const market = data.comparison?.market;
  if (!market?.length) return null;
  return {
    name: "market-tape",
    columns: [
      "step",
      "reference_price",
      "mid_price",
      "mark_price",
      "best_bid",
      "best_ask",
      "spread",
      "volatility",
      "trade_count",
      "shock_fired",
    ],
    rows: market.map((p) => [
      p.step,
      p.reference_price,
      p.mid_price,
      p.mark_price,
      p.best_bid,
      p.best_ask,
      p.spread,
      p.volatility,
      p.trade_count,
      p.shock_fired ? "true" : "false",
    ]),
  };
}

export function agentMatrixRows(data: ModuleData): CsvTable | null {
  const rows = data.comparison?.rows;
  if (!rows?.length) return null;
  return {
    name: "agent-matrix",
    columns: [
      "agent_id",
      "name",
      "agent_type",
      "total_pnl",
      "realized_pnl",
      "unrealized_pnl",
      "sharpe_ratio",
      "sortino_ratio",
      "max_drawdown_pct",
      "win_rate",
      "closed_round_trips",
      "fill_count",
      "final_inventory",
      "peak_inventory_abs",
    ],
    rows: rows.map((r) => [
      r.agent_id,
      r.name,
      r.agent_type,
      r.total_pnl,
      r.realized_pnl,
      r.unrealized_pnl,
      r.sharpe_ratio,
      r.sortino_ratio,
      r.max_drawdown_pct,
      r.win_rate,
      r.closed_round_trips,
      r.fill_count,
      r.final_inventory,
      r.peak_inventory_abs,
    ]),
  };
}

/** Long format, one row per agent per step: what a pivot table or a groupby expects. */
function performanceRows(data: ModuleData, name: string): CsvTable | null {
  if (!data.performances.length) return null;
  const rows = data.performances.flatMap((agent) =>
    agent.series.map((p) => [
      p.step,
      agent.agent_id,
      agent.name,
      agent.agent_type,
      p.inventory,
      p.realized_pnl,
      p.unrealized_pnl,
      p.total_pnl,
      p.mark_price,
      p.inventory_risk_score,
    ]),
  );
  if (!rows.length) return null;
  return {
    name,
    columns: [
      "step",
      "agent_id",
      "name",
      "agent_type",
      "inventory",
      "realized_pnl",
      "unrealized_pnl",
      "total_pnl",
      "mark_price",
      "inventory_risk_score",
    ],
    rows,
  };
}

export const pnlRows = (data: ModuleData) => performanceRows(data, "agent-pnl-by-step");
export const inventoryRows = (data: ModuleData) =>
  performanceRows(data, "agent-inventory-by-step");

export function spreadRows(data: ModuleData): CsvTable | null {
  const micro = data.microstructure;
  if (!micro) return null;
  // Every horizon, not just the one on screen: the comparison across horizons is the analysis.
  const rows = Object.entries(micro.by_agent).flatMap(([horizon, agents]) =>
    agents.map((r) => [
      horizon,
      r.agent_id,
      r.name,
      r.role,
      r.trade_count,
      r.quantity,
      r.effective_half_spread,
      r.realised_half_spread,
      r.price_impact,
      r.effective_bps,
      r.realised_bps,
      r.impact_bps,
    ]),
  );
  if (!rows.length) return null;
  return {
    name: "spread-decomposition",
    columns: [
      "horizon_steps",
      "agent_id",
      "name",
      "role",
      "trade_count",
      "quantity",
      "effective_half_spread",
      "realised_half_spread",
      "price_impact",
      "effective_bps",
      "realised_bps",
      "impact_bps",
    ],
    rows,
  };
}

export function latencyRaceRows(data: ModuleData): CsvTable | null {
  const races = data.latency?.races;
  if (!races?.length) return null;
  return {
    name: "latency-races",
    columns: [
      "step",
      "maker_agent_id",
      "maker_name",
      "taker_agent_id",
      "taker_name",
      "price",
      "quantity",
      "maker_decided_us",
      "cancel_issued_us",
      "fill_us",
      "cancel_arrival_us",
      "margin_us",
    ],
    rows: races.map((r) => [
      r.step,
      r.maker_agent_id,
      r.maker_name,
      r.taker_agent_id,
      r.taker_name,
      r.price,
      r.quantity,
      r.maker_decided_us,
      r.cancel_issued_us,
      r.fill_us,
      r.cancel_arrival_us,
      r.margin_us,
    ]),
  };
}

export function micropriceRows(data: ModuleData): CsvTable | null {
  const mp = data.microprice;
  if (!mp?.steps.length) return null;
  return {
    name: "microprice-vs-mid",
    columns: ["step", "mid", "microprice", "imbalance", "bid_quantity", "ask_quantity"],
    rows: mp.steps.map((step, i) => [
      step,
      mp.mid[i],
      mp.microprice[i],
      mp.imbalance[i],
      mp.bid_quantity[i],
      mp.ask_quantity[i],
    ]),
  };
}

export function greeksRows(data: ModuleData): CsvTable | null {
  const agents = data.greeks?.agents;
  if (!agents) return null;
  const rows = Object.entries(agents).flatMap(([agentId, attribution]) =>
    (attribution.snapshots ?? []).map((s) => [
      s.step,
      agentId,
      data.greeks?.names[agentId] ?? null,
      s.spot,
      s.portfolio_delta,
      s.portfolio_gamma,
      s.portfolio_vega,
      s.portfolio_theta,
      s.gamma_pnl,
      s.theta_pnl,
      s.vega_pnl,
      s.hedge_slippage,
    ]),
  );
  if (!rows.length) return null;
  return {
    name: "greeks-attribution",
    columns: [
      "step",
      "agent_id",
      "name",
      "spot",
      "portfolio_delta",
      "portfolio_gamma",
      "portfolio_vega",
      "portfolio_theta",
      "gamma_pnl",
      "theta_pnl",
      "vega_pnl",
      "hedge_slippage",
    ],
    rows,
  };
}
