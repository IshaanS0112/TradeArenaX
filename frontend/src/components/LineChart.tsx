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

const MAX_POINTS = 700;

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
  const visible = series.filter((s) => s.points.length > 1).map(decimate);
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

  return (
    <figure className="w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`${series.map((s) => s.label).join(", ")} over simulation steps`}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={sy(t)}
              y2={sy(t)}
              stroke="#1f2637"
              strokeWidth={1}
            />
            <text x={PAD.left - 6} y={sy(t) + 3} textAnchor="end" fontSize={10} fill="#8794ad">
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
            stroke="#3f4a63"
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
              stroke="#ef4444"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <text x={sx(step) + 3} y={PAD.top + 9} fontSize={9} fill="#ef4444">
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

        <text x={PAD.left} y={H - 5} fontSize={10} fill="#8794ad">
          step {Math.round(xMin)}
        </text>
        <text x={W - PAD.right} y={H - 5} fontSize={10} fill="#8794ad" textAnchor="end">
          {Math.round(xMax)}
        </text>
      </svg>

      <figcaption className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        {yLabel && <span className="mr-2 text-slate-400">{yLabel}</span>}
        {series.map((s) => (
          <span key={s.label} className="flex items-center gap-1.5">
            <span
              className="inline-block h-2 w-2 rounded-full"
              style={{ background: s.color }}
              aria-hidden
            />
            {s.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
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
