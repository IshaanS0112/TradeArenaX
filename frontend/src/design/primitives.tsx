/** Shared primitives. */

import type { ReactNode } from "react";

export function Stat({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "default" | "positive" | "negative" | "warn";
}) {
  const toneClass =
    tone === "positive"
      ? "text-positive"
      : tone === "negative"
        ? "text-negative"
        : tone === "warn"
          ? "text-warn"
          : "text-primary";
  return (
    <div className="min-w-0">
      <div className="truncate text-2xs uppercase tracking-wide text-muted">{label}</div>
      <div className={`num mt-0.5 truncate text-base ${toneClass}`}>{value}</div>
      {hint && <div className="mt-0.5 truncate text-2xs text-muted">{hint}</div>}
    </div>
  );
}

export function Tag({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "bid" | "ask" | "accent" | "warn";
}) {
  const tones: Record<string, string> = {
    neutral: "bg-raised text-secondary",
    bid: "bg-bid/15 text-bid",
    ask: "bg-ask/15 text-ask",
    accent: "bg-accent/15 text-accent",
    warn: "bg-warn/15 text-warn",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-2xs uppercase tracking-wide ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = "ghost",
  title,
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "ghost" | "primary" | "quiet";
  title?: string;
  disabled?: boolean;
}) {
  const variants: Record<string, string> = {
    primary:
      "bg-accent text-base hover:opacity-90 disabled:opacity-40 font-medium",
    ghost:
      "border border-edge bg-raised text-secondary hover:border-edge-focus hover:text-primary disabled:opacity-40",
    quiet: "text-muted hover:text-primary disabled:opacity-40",
  };
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-input px-2 py-1 text-xs transition-colors duration-fast ${variants[variant]}`}
    >
      {children}
    </button>
  );
}

export function Skeleton({ lines = 4 }: { lines?: number }) {
  return (
    <div className="space-y-2 p-1" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }).map((_, i) => (
        <div
          key={i}
          className="h-3 animate-pulse rounded bg-raised"
          style={{ width: `${90 - i * 7}%` }}
        />
      ))}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full min-h-[80px] items-center justify-center px-4 py-6 text-center text-xs text-muted">
      {children}
    </div>
  );
}

export function ErrorState({ endpoint, message }: { endpoint: string; message: string }) {
  return (
    <div className="space-y-1 rounded-input border border-ask/40 bg-ask/10 p-3 text-xs text-ask">
      <p className="font-medium">This module could not load.</p>
      <p className="num break-all text-2xs opacity-80">{endpoint}</p>
      <p className="text-2xs opacity-90">{message}</p>
    </div>
  );
}

/** A labelled axis caption. */
export function AxisNote({ children }: { children: ReactNode }) {
  return <p className="mt-2 text-2xs text-muted">{children}</p>;
}
