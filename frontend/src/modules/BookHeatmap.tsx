import { useEffect, useRef } from "react";

import type { ModuleData } from "./types";
import { AxisNote, EmptyState, ErrorState, Skeleton } from "../design/primitives";

/** Resting depth over time, drawn on a canvas. */
export function BookHeatmap({ data }: { data: ModuleData }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const surface = data.surface;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !surface || !surface.steps.length) return;

    const frames = surface.steps.length;
    const rows = surface.width;
    canvas.width = frames;
    canvas.height = rows;

    const context = canvas.getContext("2d");
    if (!context) return;

    const image = context.createImageData(frames, rows);
    let peak = 0;
    for (const v of surface.grid) peak = Math.max(peak, Math.abs(v));
    peak = peak || 1;

    for (let t = 0; t < frames; t += 1) {
      for (let j = 0; j < rows; j += 1) {
        const value = surface.grid[t * rows + j];
        // Row 0 of the image is the top of the panel, and the top should be the highest price.
        const y = rows - 1 - j;
        const idx = (y * frames + t) * 4;
        if (!value) {
          image.data[idx] = 10;
          image.data[idx + 1] = 13;
          image.data[idx + 2] = 19;
          image.data[idx + 3] = 255;
          continue;
        }
        // sqrt rather than linear: depth is heavily skewed, and a linear ramp renders every level but.
        const intensity = Math.min(1, Math.sqrt(Math.abs(value) / peak));
        const [r, g, b] =
          value > 0 ? [0x26, 0xd0, 0x7c] : [0xf2, 0x54, 0x4b];
        image.data[idx] = Math.round(14 + (r - 14) * intensity);
        image.data[idx + 1] = Math.round(18 + (g - 18) * intensity);
        image.data[idx + 2] = Math.round(26 + (b - 26) * intensity);
        image.data[idx + 3] = 255;
      }
    }
    context.putImageData(image, 0, 0);
  }, [surface]);

  if (data.errors.surface) {
    return <ErrorState endpoint={data.endpoints.surface} message={data.errors.surface} />;
  }
  if (data.loading.surface && !surface) return <Skeleton lines={7} />;
  if (!surface || !surface.steps.length) {
    return <EmptyState>No book history for this run yet.</EmptyState>;
  }

  const firstStep = surface.steps[0] ?? 0;
  const lastStep = surface.steps[surface.steps.length - 1] ?? firstStep;
  const span = Math.max(1, lastStep - firstStep);
  const markerPct = (step: number) => ((step - firstStep) / span) * 100;

  return (
    <div className="flex h-full flex-col">
      <div className="relative min-h-0 flex-1 overflow-hidden rounded-input border border-edge bg-base">
        <canvas
          ref={canvasRef}
          className="h-full w-full"
          style={{ imageRendering: "pixelated" }}
          onClick={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const fraction = (event.clientX - rect.left) / rect.width;
            const index = Math.max(
              0,
              Math.min(surface.steps.length - 1, Math.round(fraction * (surface.steps.length - 1))),
            );
            data.setStep(surface.steps[index] ?? firstStep);
          }}
        />
        {surface.shock_steps.map((step) => (
          <div
            key={step}
            aria-hidden
            className="pointer-events-none absolute inset-y-0 w-px bg-warn/70"
            style={{ left: `${markerPct(step)}%` }}
          />
        ))}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 w-px bg-accent"
          style={{ left: `${markerPct(data.step)}%` }}
        />
      </div>
      <AxisNote>
        x: step {firstStep}–{lastStep} (every {surface.stride}). y: ±{surface.levels} ticks
        around the mid. Green bid, red ask, brightness by resting quantity. Amber line
        is the shock; cyan is the scrubbed step — click to move it.
      </AxisNote>
    </div>
  );
}
