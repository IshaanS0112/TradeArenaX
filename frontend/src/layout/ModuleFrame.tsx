import { Suspense, type ReactNode } from "react";

import type { ModuleDefinition, ModuleWidth } from "../modules/types";
import { Button, Skeleton } from "../design/primitives";

const SPAN: Record<ModuleWidth, string> = {
  1: "lg:col-span-1",
  2: "lg:col-span-2",
  3: "lg:col-span-3",
};

export function ModuleFrame({
  definition,
  width,
  onWidth,
  onMove,
  onRemove,
  children,
}: {
  definition: ModuleDefinition;
  width: ModuleWidth;
  onWidth: (width: ModuleWidth) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  children: ReactNode;
}) {
  return (
    // The height belongs on the frame, not on the body: the body is a flex child with `flex-1`.
    <section
      className={`flex flex-col rounded-module border border-edge bg-panel ${SPAN[width]}`}
      style={{ height: definition.defaultHeight }}
    >
      <header className="flex items-start gap-2 border-b border-edge px-3 py-2">
        <div className="min-w-0">
          <h2 className="truncate text-xs font-semibold uppercase tracking-wide text-primary">
            {definition.title}
          </h2>
          <p className="truncate text-2xs text-muted">{definition.subtitle}</p>
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <Button variant="quiet" title="Move left" onClick={() => onMove(-1)}>
            ←
          </Button>
          <Button variant="quiet" title="Move right" onClick={() => onMove(1)}>
            →
          </Button>
          <Button
            variant="quiet"
            title="Cycle width"
            onClick={() => onWidth(((width % 3) + 1) as ModuleWidth)}
          >
            {width}×
          </Button>
          <Button variant="quiet" title="Remove module" onClick={onRemove}>
            ✕
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden p-3">
        <Suspense fallback={<Skeleton lines={7} />}>{children}</Suspense>
      </div>
    </section>
  );
}
