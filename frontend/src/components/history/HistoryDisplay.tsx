import { renderHistory, RenderedHistoryItem } from "./utils";
import { useProjectStore } from "../../store/project-store";
import { BsChevronDown, BsChevronRight } from "react-icons/bs";
import { useState, useRef, useEffect, useCallback } from "react";

function MediaThumbnail({
  item,
  onPlayClick,
}: {
  item: RenderedHistoryItem;
  onPlayClick?: () => void;
}) {
  const firstImage = item.images[0];
  const firstVideo = item.videos[0];

  if (!firstImage && !firstVideo) return null;

  return (
    <div className="h-12 w-12 shrink-0 overflow-hidden rounded-md border border-border bg-muted">
      {firstImage ? (
        <img
          src={firstImage}
          alt="Input screenshot"
          className="h-full w-full object-cover"
          draggable={false}
        />
      ) : firstVideo ? (
        <button
          type="button"
          className="relative h-full w-full"
          aria-label="Play video"
          onClick={(e) => {
            e.stopPropagation();
            onPlayClick?.();
          }}
        >
          <video
            src={firstVideo}
            className="h-full w-full object-cover"
            muted
            playsInline
          />
          <div className="absolute inset-0 flex items-center justify-center bg-foreground/25">
            <svg
              className="h-4 w-4 text-background"
              fill="currentColor"
              viewBox="0 0 20 20"
              aria-hidden="true"
            >
              <path d="M6.3 2.841A1.5 1.5 0 004 4.11v11.78a1.5 1.5 0 002.3 1.269l9.344-5.89a1.5 1.5 0 000-2.538L6.3 2.84z" />
            </svg>
          </div>
        </button>
      ) : null}
    </div>
  );
}

function ExpandedMedia({
  item,
  autoPlayVideo,
}: {
  item: RenderedHistoryItem;
  autoPlayVideo?: boolean;
}) {
  const hasImages = item.images.length > 0;
  const hasVideos = item.videos.length > 0;
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (autoPlayVideo && videoRef.current) {
      videoRef.current.play().catch(() => {});
    }
  }, [autoPlayVideo]);

  if (!hasImages && !hasVideos) return null;

  return (
    <div className="mt-2 flex flex-col gap-2">
      {item.images.map((img, i) => (
        <div
          key={`img-${i}`}
          className="overflow-hidden rounded-md border border-border"
        >
          <img
            src={img}
            alt={`Input ${i + 1}`}
            className="h-auto max-h-48 w-full object-contain"
            draggable={false}
          />
        </div>
      ))}
      {item.videos.map((vid, i) => (
        <div
          key={`vid-${i}`}
          className="overflow-hidden rounded-md border border-border"
        >
          <video
            ref={i === 0 ? videoRef : undefined}
            src={vid}
            className="h-auto max-h-48 w-full"
            controls
            muted
            playsInline
          />
        </div>
      ))}
    </div>
  );
}

export default function HistoryDisplay() {
  const { commits, head, setHead } = useProjectStore();
  const [expandedHash, setExpandedHash] = useState<string | null>(null);
  const [autoPlayHash, setAutoPlayHash] = useState<string | null>(null);

  // Clear auto-play flag when the expanded item changes
  useEffect(() => {
    if (expandedHash !== autoPlayHash) {
      setAutoPlayHash(null);
    }
  }, [expandedHash, autoPlayHash]);

  const handleVideoPlayClick = useCallback((hash: string) => {
    setExpandedHash(hash);
    setAutoPlayHash(hash);
  }, []);

  // Put all commits into an array and sort by created date (oldest first)
  const flatHistory = Object.values(commits).sort(
    (a, b) =>
      new Date(a.dateCreated).getTime() - new Date(b.dateCreated).getTime()
  );

  // Annotate history items with a summary, parent version, etc.
  const renderedHistory = renderHistory(flatHistory);

  if (renderedHistory.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      {[...renderedHistory].reverse().map((item, reverseIndex) => {
        const versionNumber = renderedHistory.length - reverseIndex;
        const isActive = item.hash === head;
        const isExpanded = expandedHash === item.hash;
        const hasMedia = item.images.length > 0 || item.videos.length > 0;

        return (
          <div
            key={item.hash}
            className={`rounded-xl border bg-card transition-colors ${
              isActive
                ? "border-brand-border shadow-card"
                : "border-border hover:border-input"
            }`}
          >
            {/* The whole row selects this version. The button is stretched
                behind the content so the media and expand controls, which are
                buttons themselves, stay clickable without nesting buttons. */}
            <div className="relative flex items-center gap-3 px-3 py-2.5">
              <button
                type="button"
                onClick={() => setHead(item.hash)}
                aria-label={`Open ${item.summary}`}
                aria-current={isActive ? "true" : undefined}
                className="absolute inset-0 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />

              <div className="pointer-events-none relative flex min-w-0 flex-1 items-center gap-3">
                {/* Version number badge */}
                <span
                  className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold tabular-nums ${
                    isActive
                      ? "bg-brand-subtle text-brand"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {versionNumber}
                </span>

                {/* Thumbnail */}
                <div className="pointer-events-auto">
                  <MediaThumbnail
                    item={item}
                    onPlayClick={() => handleVideoPlayClick(item.hash)}
                  />
                </div>

                {/* Summary */}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                      {item.type}
                    </span>
                    {item.parentVersion !== null && (
                      <span className="text-[10px] text-muted-foreground">
                        from v{item.parentVersion}
                      </span>
                    )}
                  </div>
                  <p
                    className={`mt-0.5 line-clamp-2 break-words text-sm ${
                      isActive
                        ? "font-medium text-foreground"
                        : "text-muted-foreground"
                    }`}
                  >
                    {item.summary}
                    {item.selectedElementTag && (
                      <>
                        {" "}
                        <span className="text-border">&middot;</span>{" "}
                        <code className="font-mono text-xs text-brand">
                          &lt;{item.selectedElementTag}&gt;
                        </code>
                      </>
                    )}
                  </p>
                </div>
              </div>

              {/* Expand button */}
              {(hasMedia || item.summary.length > 30) && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandedHash(isExpanded ? null : item.hash);
                  }}
                  aria-expanded={isExpanded}
                  aria-label={isExpanded ? "Hide details" : "Show details"}
                  className="relative shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {isExpanded ? (
                    <BsChevronDown className="h-3 w-3" aria-hidden="true" />
                  ) : (
                    <BsChevronRight className="h-3 w-3" aria-hidden="true" />
                  )}
                </button>
              )}
            </div>

            {/* Expanded details */}
            {isExpanded && (
              <div className="px-3 pb-3 pl-12">
                {item.summary.length > 30 && (
                  <p className="break-words text-xs leading-5 text-muted-foreground">
                    {item.summary}
                  </p>
                )}
                {item.selectedElementTag && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Target:{" "}
                    <code className="rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-brand">
                      &lt;{item.selectedElementTag}&gt;
                    </code>
                  </p>
                )}
                <ExpandedMedia
                  item={item}
                  autoPlayVideo={autoPlayHash === item.hash}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
