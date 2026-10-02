import type { ModuleData } from "./types";
import { AxisNote, EmptyState, ErrorState, Skeleton } from "../design/primitives";
import { num } from "../components/format";

/** Cumulative depth either side of the touch. */
export function DepthChart({ data }: { data: ModuleData }) {
  if (data.errors.book) {
    return <ErrorState endpoint={data.endpoints.book} message={data.errors.book} />;
  }
  if (data.loading.book && !data.book) return <Skeleton lines={6} />;
  const book = data.book;
  if (!book || (!book.bids.length && !book.asks.length)) {
    return <EmptyState>No resting liquidity at step {data.step}.</EmptyState>;
  }

  const prices = [...book.bids.map((b) => b.price), ...book.asks.map((a) => a.price)];
  const xMin = Math.min(...prices);
  const xMax = Math.max(...prices);
  const yMax = Math.max(
    1,
    ...book.bids.map((b) => b.cumulative_quantity),
    ...book.asks.map((a) => a.cumulative_quantity),
  );

  const W = 1000;
  const H = 260;
  const PAD = { top: 12, right: 12, bottom: 26, left: 44 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const sx = (p: number) => PAD.left + ((p - xMin) / (xMax - xMin || 1)) * plotW;
  const sy = (q: number) => PAD.top + plotH - (q / yMax) * plotH;

  const step = (
    levels: { price: number; cumulative_quantity: number }[],
    ascending: boolean,
  ) => {
    const ordered = [...levels].sort((a, b) =>
      ascending ? a.price - b.price : b.price - a.price,
    );
    if (!ordered.length) return "";
    const pts: string[] = [];
    ordered.forEach((l, i) => {
      const prev = i === 0 ? 0 : (ordered[i - 1]?.cumulative_quantity ?? 0);
      pts.push(`${sx(l.price)},${sy(prev)}`);
      pts.push(`${sx(l.price)},${sy(l.cumulative_quantity)}`);
    });
    const last = ordered[ordered.length - 1];
    if (last) pts.push(`${sx(last.price)},${sy(last.cumulative_quantity)}`);
    return pts.join(" ");
  };

  const bidPath = step(book.bids, false);
  const askPath = step(book.asks, true);

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img"
        aria-label="Cumulative order book depth by price">
        {[0, yMax / 2, yMax].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} stroke="var(--border)" />
            <text x={PAD.left - 6} y={sy(t) + 4} textAnchor="end" fontSize="11" fill="var(--fg-muted)">
              {num(t, 0)}
            </text>
          </g>
        ))}
        {book.mid_price !== null && (
          <line
            x1={sx(book.mid_price)}
            x2={sx(book.mid_price)}
            y1={PAD.top}
            y2={PAD.top + plotH}
            stroke="var(--fg-muted)"
            strokeDasharray="3 4"
          />
        )}
        {bidPath && <polyline points={bidPath} fill="none" stroke="var(--bid)" strokeWidth="2" />}
        {askPath && <polyline points={askPath} fill="none" stroke="var(--ask)" strokeWidth="2" />}
        <text x={PAD.left} y={H - 6} fontSize="11" fill="var(--fg-muted)">
          {num(xMin, 2)}
        </text>
        <text x={W - PAD.right} y={H - 6} textAnchor="end" fontSize="11" fill="var(--fg-muted)">
          {num(xMax, 2)}
        </text>
      </svg>
      <AxisNote>
        Cumulative resting quantity against price at step {data.step}. Dashed line is
        the mid; bids fill to the left, asks to the right.
      </AxisNote>
    </div>
  );
}
