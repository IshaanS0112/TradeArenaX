import { Suspense, type ReactNode } from "react";

import type { ModuleData, ModuleDefinition, ModuleWidth } from "../modules/types";
import { Button, Skeleton } from "../design/primitives";
import { downloadCsv } from "../components/csv";

const SPAN: Record<ModuleWidth, string> = {
  1: "lg:col-span-1",
  2: "lg:col-span-2",
  3: "lg:col-span-3",
};

/** 14px stroke icons, one family, so the header reads as a toolbar rather than as text. */
function Icon({ path }: { path: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
    >
      <path d={path} />
    </svg>
  );
}

const ARROW_LEFT = "M19 12H5M12 19l-7-7 7-7";
const ARROW_RIGHT = "M5 12h14M12 5l7 7-7 7";
const CLOSE = "M18 6 6 18M6 6l12 12";
const DOWNLOAD = "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3";

export function ModuleFrame({
  definition,
  data,
  width,
  onWidth,
  onMove,
  onRemove,
  children,
}: {
  definition: ModuleDefinition;
  data: ModuleData;
  width: ModuleWidth;
  onWidth: (width: ModuleWidth) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  children: ReactNode;
}) {
  // Only offer the download once the rows actually exist, so the button is never a dead end.
  const table = definition.toRows?.(data) ?? null;
  const exportable = table !== null && table.rows.length > 0;

  return (
    // The height belongs on the frame, not on the body: the body is a flex child with `flex-1`.
    <section
      className={`flex flex-col rounded-module border border-edge bg-panel ${SPAN[width]}`}
      style={{ height: definition.defaultHeight }}
      aria-label={definition.title}
    >
      <header className="flex items-start gap-2 border-b border-edge px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-xs font-semibold uppercase tracking-wide text-primary">
            {definition.title}
          </h2>
          <p className="truncate text-2xs text-muted">{definition.subtitle}</p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          {exportable && (
            <Button
              variant="quiet"
              title={`Download ${table.rows.length.toLocaleString()} rows as CSV`}
              label={`Download ${definition.title} as CSV`}
              onClick={() => downloadCsv(table, data.simulation?.name)}
            >
              <Icon path={DOWNLOAD} />
            </Button>
          )}
          <Button variant="quiet" title="Move left" onClick={() => onMove(-1)}>
            <Icon path={ARROW_LEFT} />
          </Button>
          <Button variant="quiet" title="Move right" onClick={() => onMove(1)}>
            <Icon path={ARROW_RIGHT} />
          </Button>
          <Button
            variant="quiet"
            title="Cycle width"
            label={`Width ${width} of 3, cycle`}
            onClick={() => onWidth(((width % 3) + 1) as ModuleWidth)}
          >
            {width}×
          </Button>
          <Button variant="quiet" title="Remove module" onClick={onRemove}>
            <Icon path={CLOSE} />
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden p-3">
        <Suspense fallback={<Skeleton lines={7} />}>{children}</Suspense>
      </div>
    </section>
  );
}
