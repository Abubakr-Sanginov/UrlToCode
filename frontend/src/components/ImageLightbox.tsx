import { useEffect, useRef, useState, useCallback } from "react";
import { LuMinus, LuPlus, LuX } from "react-icons/lu";
import { Dialog, DialogPortal, DialogOverlay } from "./ui/dialog";

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 10;
const DEFAULT_DISPLAY_WIDTH = 1000;

interface ImageLightboxProps {
  image: string | null;
  onClose: () => void;
}

function ImageLightbox({ image, onClose }: ImageLightboxProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1);
  const [naturalSize, setNaturalSize] = useState<{
    width: number;
    height: number;
  } | null>(null);
  const [fitScale, setFitScale] = useState(1);
  const initialZoomSet = useRef(false);

  const dragRef = useRef({
    isDragging: false,
    startX: 0,
    startY: 0,
    scrollLeft: 0,
    scrollTop: 0,
    didDrag: false,
  });

  // Reset state when image changes
  useEffect(() => {
    setZoom(1);
    setNaturalSize(null);
    setFitScale(1);
    initialZoomSet.current = false;
  }, [image]);

  const recomputeFitScale = useCallback(() => {
    if (!viewportRef.current || !naturalSize) return;

    // Subtract p-8 padding (32px each side)
    const viewportWidth = viewportRef.current.clientWidth - 64;
    const viewportHeight = viewportRef.current.clientHeight - 64;
    if (viewportWidth <= 0 || viewportHeight <= 0) return;

    const scale = Math.min(
      viewportWidth / naturalSize.width,
      viewportHeight / naturalSize.height,
      1
    );
    setFitScale(scale);

    // Set initial zoom to target DEFAULT_DISPLAY_WIDTH (only clamp to viewport width)
    if (!initialZoomSet.current) {
      initialZoomSet.current = true;
      const targetScale = DEFAULT_DISPLAY_WIDTH / naturalSize.width;
      const maxWidthScale = viewportWidth / naturalSize.width;
      const clampedScale = Math.min(targetScale, maxWidthScale);
      setZoom(Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, clampedScale / scale)));
    }
  }, [naturalSize]);

  useEffect(() => {
    if (!image) return;
    recomputeFitScale();

    const handleResize = () => recomputeFitScale();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [image, recomputeFitScale]);

  useEffect(() => {
    recomputeFitScale();
  }, [recomputeFitScale]);

  const zoomIn = () => {
    setZoom((z) => Math.min(MAX_ZOOM, Math.round((z + 0.5) * 100) / 100));
  };

  const zoomOut = () => {
    setZoom((z) => Math.max(MIN_ZOOM, Math.round((z - 0.5) * 100) / 100));
  };

  const zoomToFit = () => setZoom(1);

  const zoomToDefault = () => {
    if (!naturalSize || fitScale <= 0 || !viewportRef.current) return;
    const viewportWidth = viewportRef.current.clientWidth - 64;
    const targetScale = DEFAULT_DISPLAY_WIDTH / naturalSize.width;
    const maxWidthScale = viewportWidth / naturalSize.width;
    const clampedScale = Math.min(targetScale, maxWidthScale);
    setZoom(Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, clampedScale / fitScale)));
  };

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!viewportRef.current || e.button !== 0) return;
    dragRef.current = {
      isDragging: true,
      startX: e.clientX,
      startY: e.clientY,
      scrollLeft: viewportRef.current.scrollLeft,
      scrollTop: viewportRef.current.scrollTop,
      didDrag: false,
    };
    viewportRef.current.style.cursor = "grabbing";
    e.preventDefault();
  }, []);

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    const drag = dragRef.current;
    if (!drag.isDragging || !viewportRef.current) return;

    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;

    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      drag.didDrag = true;
    }

    viewportRef.current.scrollLeft = drag.scrollLeft - dx;
    viewportRef.current.scrollTop = drag.scrollTop - dy;
  }, []);

  const handleMouseUp = useCallback(() => {
    dragRef.current.isDragging = false;
    if (viewportRef.current) {
      viewportRef.current.style.cursor = "";
    }
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (!viewportRef.current) return;
    viewportRef.current.scrollTop += e.deltaY;
    viewportRef.current.scrollLeft += e.deltaX;
  }, []);

  const handleViewportClick = useCallback(() => {
    if (dragRef.current.didDrag) {
      dragRef.current.didDrag = false;
      return;
    }
    onClose();
  }, [onClose]);

  const effectiveScale = fitScale * zoom;
  const displayWidth = naturalSize
    ? Math.max(1, Math.round(naturalSize.width * effectiveScale))
    : undefined;
  const displayHeight = naturalSize
    ? Math.max(1, Math.round(naturalSize.height * effectiveScale))
    : undefined;

  return (
    <Dialog open={!!image} onOpenChange={(open) => !open && onClose()}>
      <DialogPortal>
        <DialogOverlay className="bg-foreground/85 backdrop-blur-sm" />
        <div className="fixed inset-0 z-50">
          {/* Scrollable viewport - drag to scroll, click to close */}
          <div
            ref={viewportRef}
            className="h-full w-full cursor-grab overflow-auto"
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onWheel={handleWheel}
            onClick={handleViewportClick}
          >
            <div className="flex min-h-full min-w-full p-8">
              {image && (
                <img
                  src={image}
                  alt="Reference image"
                  className="m-auto shrink-0 select-none rounded-lg shadow-overlay"
                  draggable={false}
                  onClick={(e) => e.stopPropagation()}
                  style={
                    displayWidth && displayHeight
                      ? {
                          width: `${displayWidth}px`,
                          height: `${displayHeight}px`,
                          maxWidth: "none",
                          maxHeight: "none",
                        }
                      : { visibility: "hidden" as const }
                  }
                  onLoad={(event) => {
                    setNaturalSize({
                      width: event.currentTarget.naturalWidth,
                      height: event.currentTarget.naturalHeight,
                    });
                  }}
                />
              )}
            </div>
          </div>

          {/* Zoom controls - bottom center pill */}
          <div
            className="absolute bottom-6 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1 rounded-full border border-border bg-card px-2.5 py-1.5 shadow-overlay"
            onClick={(e) => e.stopPropagation()}
            onMouseDown={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={zoomOut}
              className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              disabled={zoom <= MIN_ZOOM}
              title="Zoom out"
              aria-label="Zoom out"
            >
              <LuMinus className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={zoomToDefault}
              className="min-w-[3.5rem] rounded-full px-3 py-1 text-center text-xs font-medium tabular-nums text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              title="Reset zoom"
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              onClick={zoomIn}
              className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              disabled={zoom >= MAX_ZOOM}
              title="Zoom in"
              aria-label="Zoom in"
            >
              <LuPlus className="h-4 w-4" aria-hidden="true" />
            </button>
            <div className="mx-1 h-4 w-px bg-border" />
            <button
              type="button"
              onClick={zoomToFit}
              className="rounded-full px-2.5 py-1 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              title="Fit to screen"
            >
              Fit
            </button>
            <div className="mx-1 h-4 w-px bg-border" />
            <button
              type="button"
              onClick={onClose}
              className="rounded-full p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              title="Close"
              aria-label="Close"
            >
              <LuX className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      </DialogPortal>
    </Dialog>
  );
}

export default ImageLightbox;
