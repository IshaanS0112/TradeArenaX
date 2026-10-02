/** Display helpers. */

export const DASH = "—";

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${value >= 0 ? "+" : ""}${num(value, digits)}`;
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${num(value, digits)}%`;
}

export function ratePct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${num(value * 100, digits)}%`;
}

export function pnlClass(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "text-muted";
  if (value > 0) return "text-positive";
  if (value < 0) return "text-negative";
  return "text-slate-300";
}

export const AGENT_LABEL: Record<string, string> = {
  MARKET_MAKER: "Market maker",
  MOMENTUM: "Momentum",
  MEAN_REVERSION: "Mean reversion",
};

export const AGENT_COLOR: Record<string, string> = {
  MARKET_MAKER: "var(--accent)",
  MOMENTUM: "var(--warn)",
  MEAN_REVERSION: "var(--info)",
};
