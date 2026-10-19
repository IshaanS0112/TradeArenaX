/** A minimal multi-series SVG line chart.
 *
 * Hand-rolled rather than pulling in a charting library: the only thing needed
 * here is a polyline over a shared x-axis with shock markers, and a dependency
 * that ships a canvas renderer and a theming system to draw four lines is not a
 * trade worth making in a project whose point is the engine.
 *
 * Long series are decimated to at most ~700 points before rendering. A 20,000
 * step run would otherwise emit a polyline with 20,000 coordinate pairs per
 * series, which the browser will draw at sub-pixel spacing - invisible detail at
 * a real cost.
 */

import { useMemo, useRef, useState } from "react";

const MAX_POINTS = 700;

/* Chart chrome reads from the design tokens, so a palette change reaches the charts too. */
const GRID = "var(--border)";
const AXIS_TEXT = "var(--fg-secondary)";
const ZERO = "var(--border-focus)";
const SHOCK = "var(--ask)";

export interface Series {
  label: string;
  color: string;
  points: { x: number; y: number }[];
}

export function LineChart({
  series,
  height = 220,
  markers = [],
  yLabel,
  zeroLine = false,
}: {
  series: Series[];
  height?: number;
  markers?: number[];
  yLabel?: string;
  zeroLine?: boolean;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  // null means "no cursor": the legend then reads the last value of each series.
  const [cursorX, setCursorX] = useState<number | null>(null);

  const visible = useMemo(
    () => series.filter((s) => s.points.length > 1).map(decimate),
    [series],
  );
  if (!visible.length) {
    return <p className="py-8 text-center text-sm text-muted">Not enough data to plot.</p>;
  }

  const all = visible.flatMap((s) => s.points);
  const xMin = Math.min(...all.map((p) => p.x));
  const xMax = Math.max(...all.map((p) => p.x));
  let yMin = Math.min(...all.map((p) => p.y));
  let yMax = Math.max(...all.map((p) => p.y));
  if (zeroLine) {
    yMin = Math.min(yMin, 0);
    yMax = Math.max(yMax, 0);
  }
  if (yMax - yMin < 1e-9) {
    // A perfectly flat series would collapse to a zero-height viewBox and
    // divide by zero below. Pad it so the line renders in the middle.
    yMax += 1;
    yMin -= 1;
  }

  const W = 1000;
  const H = height;
  const PAD = { top: 10, right: 8, bottom: 20, left: 48 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const sx = (x: number) => PAD.left + ((x - xMin) / (xMax - xMin || 1)) * plotW;
  const sy = (y: number) => PAD.top + plotH - ((y - yMin) / (yMax - yMin)) * plotH;

  const ticks = [yMin, yMin + (yMax - yMin) / 2, yMax];

  // Each series snaps to its own nearest sample: they are decimated independently,
  // so a shared index would read different steps off different lines.
  const readout = visible.map((s) => ({
    label: s.label,
    color: s.color,
    point: cursorX === null ? s.points[s.points.length - 1] : nearest(s.points, cursorX),
  }));
  const cursorStep = cursorX === null ? null : Math.round(cursorX);

  /** Client pixels -> viewBox units -> data units. */
  const moveCursor = (clientX: number) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box || box.width === 0) return;
    const frac = (((clientX - box.left) / box.width) * W - PAD.left) / (plotW || 1);
    setCursorX(frac < 0 || frac > 1 ? null : xMin + frac * (xMax - xMin));
  };

  return (
    <figure className="w-full">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full touch-none focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
        role="img"
        tabIndex={0}
        onPointerMove={(event) => moveCursor(event.clientX)}
        onMouseMove={(event) => moveCursor(event.clientX)}
        onPointerLeave={() => setCursorX(null)}
        onBlur={() => setCursorX(null)}
        onKeyDown={(event) => {
          // Arrow keys step the cursor, so the values are reachable without a pointer.
          const span = xMax - xMin || 1;
          const nudge = span / 60;
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            event.preventDefault();
            const base = cursorX ?? xMax;
            const next = base + (event.key === "ArrowRight" ? nudge : -nudge);
            setCursorX(Math.min(xMax, Math.max(xMin, next)));
          } else if (event.key === "Escape") {
            setCursorX(null);
          }
        }}
        aria-label={`${series.map((s) => s.label).join(", ")} over simulation steps`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={sy(t)}
              y2={sy(t)}
              stroke={GRID}
              strokeWidth={1}
            />
            <text x={PAD.left - 6} y={sy(t) + 3} textAnchor="end" fontSize={10} fill={AXIS_TEXT}>
              {compact(t)}
            </text>
          </g>
        ))}

        {zeroLine && yMin < 0 && yMax > 0 && (
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={sy(0)}
            y2={sy(0)}
            stroke={ZERO}
            strokeWidth={1}
            strokeDasharray="4 3"
          />
        )}

        {markers.map((step) => (
          <g key={step}>
            <line
              x1={sx(step)}
              x2={sx(step)}
              y1={PAD.top}
              y2={PAD.top + plotH}
              stroke={SHOCK}
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <text x={sx(step) + 3} y={PAD.top + 9} fontSize={9} fill={SHOCK}>
              shock
            </text>
          </g>
        ))}

        {visible.map((s) => (
          <polyline
            key={s.label}
            fill="none"
            stroke={s.color}
            strokeWidth={1.6}
            strokeLinejoin="round"
            points={s.points.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" ")}
          />
        ))}

        {cursorX !== null && (
          <g aria-hidden>
            <line
              x1={sx(cursorX)}
              x2={sx(cursorX)}
              y1={PAD.top}
              y2={PAD.top + plotH}
              stroke={AXIS_TEXT}
              strokeWidth={1}
              opacity={0.5}
            />
            {readout.map(
              (r) =>
                r.point && (
                  <circle
                    key={r.label}
                    cx={sx(r.point.x)}
                    cy={sy(r.point.y)}
                    r={3}
                    fill={r.color}
                  />
                ),
            )}
          </g>
        )}

        <text x={PAD.left} y={H - 5} fontSize={10} fill={AXIS_TEXT}>
          step {Math.round(xMin)}
        </text>
        <text x={W - PAD.right} y={H - 5} fontSize={10} fill={AXIS_TEXT} textAnchor="end">
          {Math.round(xMax)}
        </text>
      </svg>

      <figcaption className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        {yLabel && <span className="mr-2 text-slate-400">{yLabel}</span>}
        <span className="num text-secondary">
          {cursorStep === null ? "final" : `step ${cursorStep}`}
        </span>
        {readout.map((r) => (
          <span key={r.label} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: r.color }}
              aria-hidden
            />
            {r.label}
            {r.point && <span className="num text-primary">{compact(r.point.y)}</span>}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** Binary search for the sample closest to `x`; the points are sorted by step. */
function nearest(points: { x: number; y: number }[], x: number) {
  let lo = 0;
  let hi = points.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (points[mid]!.x < x) lo = mid;
    else hi = mid;
  }
  const a = points[lo]!;
  const b = points[hi]!;
  return Math.abs(a.x - x) <= Math.abs(b.x - x) ? a : b;
}

function decimate(s: Series): Series {
  if (s.points.length <= MAX_POINTS) return s;
  const stride = Math.ceil(s.points.length / MAX_POINTS);
  const points = s.points.filter((_, i) => i % stride === 0);
  const last = s.points[s.points.length - 1];
  // Always keep the final point: the end of a PnL curve is the number in the
  // comparison table, and dropping it makes the chart disagree with the table.
  if (last && points[points.length - 1] !== last) points.push(last);
  return { ...s, points };
}

function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  if (abs >= 10) return value.toFixed(0);
  return value.toFixed(2);
}
