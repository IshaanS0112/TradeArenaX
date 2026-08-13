export type AgentType = "MARKET_MAKER" | "MOMENTUM" | "MEAN_REVERSION";

export interface PriceProcessConfig {
  initial_price: number;
  drift: number;
  volatility: number;
  random_seed: number;
}

export interface ShockConfig {
  step: number;
  magnitude_pct: number;
  vol_multiplier: number;
  vol_half_life_steps: number;
}

export interface Agent {
  id: string;
  simulation_id: string;
  name: string;
  agent_type: AgentType;
  config: Record<string, unknown>;
  final_metrics: AgentMetrics;
  risk_flags: string[];
  created_at: string;
}

/** Every field may be null: an agent that never closed a trade has no win rate,
 *  and a PnL series with no dispersion has no Sharpe ratio. Rendering 0 in those
 *  cases would state something the data does not support. */
export interface AgentMetrics {
  total_pnl?: number;
  realized_pnl?: number;
  unrealized_pnl?: number;
  fees_paid?: number;
  final_inventory?: number;
  sharpe_ratio?: number | null;
  sharpe_ratio_per_step?: number | null;
  sortino_ratio?: number | null;
  max_drawdown_pct?: number | null;
  max_drawdown_abs?: number | null;
  volatility_annualised?: number | null;
  win_rate?: number | null;
  closed_round_trips?: number;
  fill_count?: number;
  total_return_pct?: number;
  peak_inventory_abs?: number;
  max_inventory_risk_score?: number | null;
  notes?: string[];
  name?: string;
}

export interface Simulation {
  id: string;
  name: string;
  duration_steps: number;
  status: "CREATED" | "COMPLETED";
  price_process_config: PriceProcessConfig;
  volatility_shock_config: { shocks?: ShockConfig[] };
  engine_config: Record<string, number | string>;
  run_summary: RunSummary | Record<string, never>;
  created_at: string;
  agents: Agent[];
}

export interface RunSummary {
  simulation_id: string;
  steps_run: number;
  total_trades: number;
  total_orders: number;
  configured_volatility: number;
  realized_volatility: number | null;
  steps_with_no_two_sided_book: number;
  final_reference_price: number;
  final_mid_price: number | null;
  warnings: string[];
  agent_metrics: Record<string, AgentMetrics>;
}

export interface BookLevel {
  price: number;
  quantity: number;
  cumulative_quantity: number;
  order_count: number;
}

export interface OrderBookSnapshot {
  bids: BookLevel[];
  asks: BookLevel[];
  best_bid: number | null;
  best_ask: number | null;
  mid_price: number | null;
  spread: number | null;
  total_trades: number;
  reconstructed_at_step: number | null;
}

export interface PerformancePoint {
  step: number;
  inventory: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  mark_price: number | null;
  inventory_risk_score: number;
}

export interface AgentPerformance {
  agent_id: string;
  agent_type: AgentType;
  name: string;
  config: Record<string, unknown>;
  metrics: AgentMetrics;
  risk_flags: string[];
  series: PerformancePoint[];
}

export interface ComparisonRow {
  agent_id: string;
  name: string;
  agent_type: AgentType;
  total_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  max_drawdown_pct: number | null;
  win_rate: number | null;
  closed_round_trips: number;
  fill_count: number;
  final_inventory: number;
  peak_inventory_abs: number;
  max_inventory_risk_score: number | null;
  fees_paid: number;
  notes: string[];
}

export interface StepPoint {
  step: number;
  reference_price: number;
  mid_price: number | null;
  /** The mark actually used for unrealized PnL that step: the mid when there was
   *  one, else the last trade. Never null. */
  mark_price: number | null;
  best_bid: number | null;
  best_ask: number | null;
  spread: number | null;
  volatility: number;
  trade_count: number;
  shock_fired: boolean;
}

export interface Comparison {
  simulation_id: string;
  steps_run: number;
  rows: ComparisonRow[];
  market: StepPoint[];
  shock_steps: number[];
  pnl_conservation_residual: number;
  total_fees_collected: number;
  warnings: string[];
}

export type AgentDefaults = Record<AgentType, Record<string, number | string | boolean>>;
