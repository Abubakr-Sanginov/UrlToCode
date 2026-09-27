import { useProjectStore } from "../../store/project-store";
import { useEffect, useRef, useState } from "react";
import { useThrottle } from "../../hooks/useThrottle";
import {
  CODE_GENERATION_MODEL_DESCRIPTIONS,
  CodeGenerationModel,
  getVariantLabel,
  VariantLabelTone,
} from "../../lib/models";
import WorkingPulse from "../core/WorkingPulse";

const IFRAME_WIDTH = 1280;
const IFRAME_HEIGHT = 550;

// Badge shown in the corner of each variant thumbnail. Neutral by default so
// it labels the option without competing with the preview.
const BADGE_TONE: Record<VariantLabelTone, string> = {
  fast: "bg-card/90 text-muted-foreground",
  max: "bg-brand/90 text-brand-foreground",
};

interface VariantThumbnailProps {
  code: string;
  isSelected: boolean;
}

function VariantThumbnail({ code, isSelected }: VariantThumbnailProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [scale, setScale] = useState(0.1);

  const throttledCode = useThrottle(code, isSelected ? 300 : 2000);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const updateScale = () => {
      const containerWidth = container.offsetWidth;
      setScale(containerWidth / IFRAME_WIDTH);
    };

    updateScale();
    const resizeObserver = new ResizeObserver(updateScale);
    resizeObserver.observe(container);

    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (iframe) {
      iframe.srcdoc = throttledCode;
    }
  }, [throttledCode]);

  const scaledHeight = IFRAME_HEIGHT * scale;

  return (
    <div
      ref={containerRef}
      className="w-full overflow-hidden rounded border border-border bg-background"
      style={{ height: `${scaledHeight}px` }}
    >
      <iframe
        ref={iframeRef}
        title="variant-preview"
        className="pointer-events-none origin-top-left"
        style={{
          width: `${IFRAME_WIDTH}px`,
          height: `${IFRAME_HEIGHT}px`,
          transform: `scale(${scale})`,
        }}
        sandbox="allow-scripts allow-same-origin"
      />
    </div>
  );
}

function Variants() {
  const { head, commits, updateSelectedVariantIndex, inputMode } =
    useProjectStore();

  const commit = head ? commits[head] : null;
  const variants = commit?.variants || [];
  const selectedVariantIndex = commit?.selectedVariantIndex || 0;
  const generationType: "create" | "update" =
    commit?.type === "ai_create" ? "create" : "update";

  const handleVariantClick = (index: number) => {
    if (index === selectedVariantIndex || !head) return;
    updateSelectedVariantIndex(head, index);
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.altKey && !event.ctrlKey && !event.shiftKey && !event.metaKey) {
        const code = event.code;
        if (code >= "Digit1" && code <= "Digit9") {
          const variantIndex = parseInt(code.replace("Digit", "")) - 1;
          if (
            commit &&
            variantIndex < variants.length &&
            variants.length > 1 &&
            !commit.isCommitted
          ) {
            event.preventDefault();
            handleVariantClick(variantIndex);
          }
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
    // handleVariantClick and commit are read through the listener at the
    // moment a key is pressed; adding them would re-register the listener on
    // every render for no behavioural gain.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [variants.length, commit?.isCommitted, selectedVariantIndex, head]);

  if (head === null || !commit) {
    return null;
  }

  if (variants.length <= 1 || commit.isCommitted) {
    return <div className="mt-2"></div>;
  }

  return (
    <div className="pb-1 pt-2">
      <div className="grid grid-cols-2 gap-2">
        {variants.map((variant, index) => {
          let statusColor = "bg-border";
          if (variant.status === "complete") statusColor = "bg-success";
          else if (variant.status === "error" || variant.status === "cancelled")
            statusColor = "bg-danger";

          const label = getVariantLabel(variant.model, {
            inputMode,
            generationType,
          });

          return (
            <button
              key={index}
              type="button"
              aria-pressed={index === selectedVariantIndex}
              className={`relative w-full overflow-hidden rounded-lg text-left transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                index === selectedVariantIndex
                  ? "ring-2 ring-brand"
                  : "ring-1 ring-border hover:ring-input"
              }`}
              title={
                variant.model
                  ? CODE_GENERATION_MODEL_DESCRIPTIONS[
                      variant.model as CodeGenerationModel
                    ]?.name || variant.model
                  : undefined
              }
              onClick={() => handleVariantClick(index)}
            >
              {label && (
                <span
                  className={`absolute right-1.5 top-1.5 z-10 rounded px-1.5 py-0.5 text-[10px] font-medium leading-none ${BADGE_TONE[label.tone]}`}
                >
                  {label.text}
                </span>
              )}
              <VariantThumbnail
                code={variant.code}
                isSelected={index === selectedVariantIndex}
              />
              <div className="flex items-center bg-card px-2 py-1">
                <span className="inline-flex min-w-0 items-center whitespace-nowrap text-xs text-muted-foreground">
                  <span
                    className={`mr-1.5 h-1.5 w-1.5 rounded-full ${statusColor}`}
                    aria-hidden="true"
                  />
                  Option {index + 1}
                  {index < 9 && (
                    <span className="ml-1 font-mono text-[11px] text-muted-foreground/70">
                      ⌥{index + 1}
                    </span>
                  )}
                </span>
                {variant.status === "generating" && (
                  <div
                    className="ml-auto inline-flex shrink-0 items-center"
                    role="status"
                    aria-live="polite"
                    aria-label="Working"
                  >
                    <WorkingPulse />
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default Variants;
