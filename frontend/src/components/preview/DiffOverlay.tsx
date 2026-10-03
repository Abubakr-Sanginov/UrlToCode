import { useEffect, useMemo, useRef, useState } from "react";
import { LuMoveHorizontal } from "react-icons/lu";
import { DiffRegion, ViewportFidelity, fidelityVerdict, fidelityLabel } from "../../lib/cloneRuns";

/**
 * The original and the clone, one on top of the other, with a slider between
 * them.
 *
 * Two pages side by side answer "are these the same site"; this answers "where
 * exactly do they stop being the same", which is the question a repair needs.
 * The slider wipes between the two pictures, and the areas the check found
 * different are outlined on top, so the eye is sent to the part that is wrong
 * instead of hunting for it across a full-page screenshot.
 */

interface ViewportRow {
  name: string;
  label: string;
  score: number;
}

const VIEWPORT_LABELS: Record<string, string> = {
  desktop: "Desktop",
  tablet: "Tablet",
  mobile: "Mobile",
};

interface Props {
  originalUrl: string;
  cloneUrl: string;
  score: number | null;
  regions: DiffRegion[];
  /** The original picture's real pixel size, which the regions are in. */
  originalWidth: number;
  originalHeight: number;
  /** The clone's real pixel size, which tells the two apart at a glance. */
  renderedHeight: number;
  /** One row per width checked, so a phone-only failure is visible. */
  viewports?: ViewportFidelity[];
  threshold?: number;
  onRegionHover?: (region: DiffRegion | null) => void;
}

const DEFAULT_THRESHOLD = 0.8;

/** Drags the wipe: a percentage of the width, or null when the pointer is up. */
function useDrag(onMove: (percent: number) => void) {
  const [dragging, setDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!dragging) return;

    const move = (event: PointerEvent) => {
      const box = containerRef.current?.getBoundingClientRect();
      if (!box || box.width === 0) return;
      // The wipe has to stay inside the picture: a handle dragged past the
      // edge would otherwise hide one of the two completely.
      const percent = Math.max(0, Math.min(100, ((event.clientX - box.left) / box.width) * 100));
      onMove(percent);
    };
    const stop = () => setDragging(false);

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, [dragging, onMove]);

  return { containerRef, setDragging };
}

export default function DiffOverlay({
  originalUrl,
  cloneUrl,
  score,
  regions,
  originalWidth,
  originalHeight,
  renderedHeight,
  viewports = [],
  threshold = DEFAULT_THRESHOLD,
  onRegionHover,
}: Props) {
  const [position, setPosition] = useState(50);
  const [showRegions, setShowRegions] = useState(true);
  const { containerRef, setDragging } = useDrag(setPosition);

  const verdict = useMemo(() => fidelityVerdict(score, threshold), [score, threshold]);

  // Widths that could not be compared are left out: showing a 0% for a page
  // that was never checked would read as a failure that did not happen.
  const viewportRows = useMemo<ViewportRow[]>(
    () =>
      viewports
        .filter((entry) => !entry.error)
        .map((entry) => ({
          name: entry.name,
          label: VIEWPORT_LABELS[entry.name] ?? entry.name,
          score: entry.score,
        })),
    [viewports]
  );

  // The container is as tall as the original, so the regions - which are in
  // the original's own pixels - land where they belong at any width. A clone
  // shorter than the original simply ends early, which is itself the clearest
  // thing to see in a diff.
  const outline = useMemo(
    () =>
      regions.map((region, index) => ({
        region,
        key: `${index}-${region.x}-${region.y}`,
        left: originalWidth ? (region.x / originalWidth) * 100 : 0,
        top: originalHeight ? (region.y / originalHeight) * 100 : 0,
        width: originalWidth ? (region.width / originalWidth) * 100 : 0,
        // A region that starts inside the page but runs past the bottom is
        // the clone running out of content, and that is worth showing.
        height: originalHeight ? (region.height / originalHeight) * 100 : 0,
      })),
    [regions, originalWidth, originalHeight]
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        <span className="font-medium uppercase tracking-wide">Diff</span>
        <div className="flex items-center gap-3">
          {score !== null && (
            <span
              data-testid="diff-score"
              className={
                verdict === "close"
                  ? "text-emerald-500"
                  : verdict === "off"
                    ? "text-red-500"
                    : "text-amber-500"
              }
              title={fidelityLabel(verdict)}
            >
              {Math.round(score * 100)}% match
            </span>
          )}
          {regions.length > 0 && (
            <label className="flex cursor-pointer items-center gap-1.5 select-none">
              <input
                type="checkbox"
                checked={showRegions}
                onChange={(event) => setShowRegions(event.target.checked)}
                className="h-3 w-3 accent-foreground"
                data-testid="toggle-diff-regions"
              />
              <span>
                {regions.length} differing {regions.length === 1 ? "area" : "areas"}
              </span>
            </label>
          )}
        </div>
      </div>

      {/* When the page was checked at several widths, the one on screen is
          not the whole story: a clone can be right on desktop and wrong on a
          phone. These rows say which. */}
      {viewportRows.length > 1 && (
        <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-3 py-1.5 text-[11px]">
          {viewportRows.map((row) => (
            <span key={row.name} className="flex items-center gap-1.5">
              <span className="text-muted-foreground">{row.label}</span>
              <span
                className={
                  row.score >= 0.95
                    ? "text-emerald-500"
                    : row.score >= 0.8
                      ? "text-amber-500"
                      : "text-red-500"
                }
                data-testid={`diff-score-${row.name}`}
              >
                {Math.round(row.score * 100)}%
              </span>
            </span>
          ))}
        </div>
      )}

      <div className="flex min-h-0 flex-1 flex-col">
        <div
          ref={containerRef}
          data-testid="diff-overlay"
          className="relative min-h-0 flex-1 overflow-auto bg-muted/30 select-none"
        >
          <div
            className="relative mx-auto w-full"
            style={{
              // The picture keeps the original's proportions, so the wipe lines
              // up with what is underneath it.
              aspectRatio: `${originalWidth || 1366} / ${originalHeight || 1000}`,
            }}
          >
            <img
              src={originalUrl}
              alt="Screenshot of the original page"
              className="absolute inset-0 h-full w-full object-cover object-top"
              draggable={false}
            />
            <div
              className="absolute inset-0 overflow-hidden"
              style={{
                clipPath: `inset(0 0 0 ${position}%)`,
              }}
              data-testid="diff-clone-layer"
            >
              <div
                className="absolute inset-x-0 top-0"
                // The clone is only as tall as the clone rendered. Below that
                // the original shows through, which is what a page that is
                // missing its lower half looks like.
                style={{
                  height: originalHeight
                    ? `${Math.min(100, (renderedHeight / originalHeight) * 100)}%`
                    : "100%",
                }}
              >
                <img
                  src={cloneUrl}
                  alt="Screenshot of the clone's page"
                  className="h-full w-full object-cover object-top"
                  draggable={false}
                />
              </div>
            </div>

            {showRegions &&
              outline.map(({ region, key, left, top, width, height }) => (
                <div
                  key={key}
                  onMouseEnter={() => onRegionHover?.(region)}
                  onMouseLeave={() => onRegionHover?.(null)}
                  className="pointer-events-auto absolute rounded-sm border-2 transition-colors"
                  style={{
                    left: `${left}%`,
                    top: `${top}%`,
                    width: `${width}%`,
                    height: `${height}%`,
                    // How wrong it is, shown as how loudly it is outlined: a
                    // barely-off area should not shout like a broken section.
                    borderColor:
                      region.severity >= 0.6
                        ? "rgba(239, 68, 68, 0.95)"
                        : "rgba(245, 158, 11, 0.9)",
                    backgroundColor:
                      region.severity >= 0.6
                        ? "rgba(239, 68, 68, 0.12)"
                        : "rgba(245, 158, 11, 0.1)",
                  }}
                  title={`${Math.round(region.severity * 100)}% of this area differs`}
                />
              ))}

            <div
              className="pointer-events-none absolute inset-y-0 w-0.5 bg-foreground/80"
              style={{ left: `${position}%` }}
            />
            <button
              type="button"
              onPointerDown={() => setDragging(true)}
              onKeyDown={(event) => {
                if (event.key === "ArrowLeft") setPosition((value) => Math.max(0, value - 5));
                if (event.key === "ArrowRight") setPosition((value) => Math.min(100, value + 5));
              }}
              aria-label="Drag to wipe between the original and the clone"
              aria-valuenow={Math.round(position)}
              role="slider"
              tabIndex={0}
              data-testid="diff-slider"
              className="absolute top-1/2 z-10 flex h-8 w-8 -translate-x-1/2 -translate-y-1/2 cursor-ew-resize items-center justify-center rounded-full border border-border bg-background text-foreground shadow-md"
              style={{ left: `${position}%` }}
            >
              <LuMoveHorizontal className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
        <p className="shrink-0 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
          Drag the handle to wipe between the original and the clone. Arrow keys move it.
        </p>
      </div>
    </div>
  );
}
