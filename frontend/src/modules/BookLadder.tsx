import type { ModuleData } from "./types";
import { EmptyState, ErrorState, Skeleton } from "../design/primitives";
import { num } from "../components/format";

/** The touch and the levels behind it, at the scrubbed step. */
export function BookLadder({ data }: { data: ModuleData }) {
  if (data.errors.book) {
    return <ErrorState endpoint={data.endpoints.book} message={data.errors.book} />;
  }
  if (data.loading.book && !data.book) return <Skeleton lines={8} />;
  if (!data.book) return <EmptyState>Run the simulation to rebuild its book.</EmptyState>;

  const { bids, asks, best_bid, best_ask, mid_price, spread } = data.book;
  if (!bids.length && !asks.length) {
    return (
      <EmptyState>
        No resting liquidity at step {data.step}. Every quote was filled or pulled.
      </EmptyState>
    );
  }

  const peak = Math.max(1, ...bids.map((b) => b.quantity), ...asks.map((a) => a.quantity));
  const rows = [
    ...[...asks].slice(0, 8).reverse().map((l) => ({ ...l, side: "ask" as const })),
    ...bids.slice(0, 8).map((l) => ({ ...l, side: "bid" as const })),
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-baseline gap-4 border-b border-edge pb-2 text-2xs uppercase tracking-wide text-muted">
        <span>Price</span>
        <span className="ml-auto">Qty</span>
        <span className="w-10 text-right">Orders</span>
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto">
        {rows.map((level) => (
          <div
            key={`${level.side}-${level.price}`}
            className="relative flex items-center gap-4 border-b border-edge/40 px-1 py-[3px] text-xs"
          >
            <div
              aria-hidden
              className={`absolute inset-y-0 right-0 ${
                level.side === "bid" ? "bg-bid/15" : "bg-ask/15"
              }`}
              style={{ width: `${(level.quantity / peak) * 100}%` }}
            />
            <span
              className={`num relative z-10 w-20 ${
                level.side === "bid" ? "text-bid" : "text-ask"
              }`}
            >
              {num(level.price, 2)}
            </span>
            <span className="num relative z-10 ml-auto text-primary">
              {num(level.quantity, 0)}
            </span>
            <span className="num relative z-10 w-10 text-right text-muted">
              {level.order_count}
            </span>
          </div>
        ))}
      </div>

      <div className="mt-2 grid grid-cols-4 gap-2 border-t border-edge pt-2 text-2xs">
        <Cell label="Bid" value={num(best_bid, 2)} tone="text-bid" />
        <Cell label="Ask" value={num(best_ask, 2)} tone="text-ask" />
        <Cell label="Mid" value={num(mid_price, 3)} />
        <Cell label="Spread" value={num(spread, 3)} />
      </div>
    </div>
  );
}

function Cell({ label, value, tone = "text-primary" }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="uppercase tracking-wide text-muted">{label}</div>
      <div className={`num ${tone}`}>{value}</div>
    </div>
  );
}
