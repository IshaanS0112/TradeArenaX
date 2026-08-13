import type { OrderBookSnapshot } from "../api/types";
import { Empty, Panel } from "./Panel";
import { num } from "./format";

/** Depth ladder.
 *
 * Bars are scaled to the largest single-level quantity across *both* sides, not
 * per side. Scaling each side independently makes a 5-lot bid look the same size
 * as a 500-lot offer, which is the one thing a depth display exists to show.
 */
export function OrderBookView({
  snapshot,
  step,
  maxStep,
  onStepChange,
}: {
  snapshot: OrderBookSnapshot | null;
  step: number;
  maxStep: number;
  onStepChange: (step: number) => void;
}) {
  const levels = snapshot ? [...snapshot.bids, ...snapshot.asks] : [];
  const peak = levels.reduce((acc, l) => Math.max(acc, l.quantity), 0) || 1;

  return (
    <Panel
      title="Order book"
      subtitle={
        snapshot
          ? `Reconstructed from the persisted order stream at step ${snapshot.reconstructed_at_step}`
          : "Run the simulation to populate the book"
      }
      actions={
        maxStep > 0 && (
          <div className="flex items-center gap-2">
            <input
              type="range"
              min={1}
              max={maxStep}
              value={step}
              onChange={(e) => onStepChange(Number(e.target.value))}
              className="w-40 accent-accent"
              aria-label="Book reconstruction step"
            />
            <span className="num w-16 text-right text-xs text-muted">
              {step}/{maxStep}
            </span>
          </div>
        )
      }
    >
      {!snapshot ? (
        <Empty>No book state yet.</Empty>
      ) : (
        <>
          <div className="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
            <Stat label="Best bid" value={num(snapshot.best_bid)} className="text-bid" />
            <Stat label="Best ask" value={num(snapshot.best_ask)} className="text-ask" />
            <Stat label="Mid" value={num(snapshot.mid_price)} />
            <Stat label="Spread" value={num(snapshot.spread, 3)} />
            <Stat label="Trades" value={num(snapshot.total_trades, 0)} />
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Ladder title="Bids" levels={snapshot.bids} peak={peak} side="bid" />
            <Ladder title="Asks" levels={snapshot.asks} peak={peak} side="ask" />
          </div>

          {snapshot.bids.length === 0 && snapshot.asks.length === 0 && (
            <p className="mt-3 text-xs text-caution">
              Both sides are empty at this step. With no resting quotes there is no
              mid price, so unrealized PnL for this step was marked against the last
              trade instead.
            </p>
          )}
        </>
      )}
    </Panel>
  );
}

function Ladder({
  title,
  levels,
  peak,
  side,
}: {
  title: string;
  levels: OrderBookSnapshot["bids"];
  peak: number;
  side: "bid" | "ask";
}) {
  const bar = side === "bid" ? "bg-bid/25" : "bg-ask/25";
  const text = side === "bid" ? "text-bid" : "text-ask";

  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px] uppercase tracking-wide text-muted">
        <span>{title}</span>
        <span>qty · orders</span>
      </div>
      {levels.length === 0 ? (
        <p className="py-3 text-xs text-muted">Empty</p>
      ) : (
        <ul className="space-y-0.5">
          {levels.map((level) => (
            <li key={level.price} className="relative overflow-hidden rounded">
              <div
                className={`absolute inset-y-0 left-0 ${bar}`}
                style={{ width: `${(level.quantity / peak) * 100}%` }}
                aria-hidden
              />
              <div className="relative flex justify-between px-2 py-1 text-xs">
                <span className={`num ${text}`}>{num(level.price)}</span>
                <span className="num text-slate-300">
                  {num(level.quantity, 1)}
                  <span className="ml-2 text-muted">{level.order_count}</span>
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  className = "text-slate-200",
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <span>
      <span className="text-muted">{label} </span>
      <span className={`num ${className}`}>{value}</span>
    </span>
  );
}
