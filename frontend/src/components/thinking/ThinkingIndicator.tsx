import { useState } from "react";
import { useProjectStore } from "../../store/project-store";
import { BsChevronDown, BsChevronRight } from "react-icons/bs";
import ReactMarkdown from "react-markdown";

function getLastSentence(text: string): string {
  const sentences = text.split(/(?<=[.!?])\s+/);
  for (let i = sentences.length - 1; i >= 0; i--) {
    const sentence = sentences[i].trim();
    if (sentence.length > 0) {
      if (sentence.length > 150) {
        return "..." + sentence.slice(-150);
      }
      return sentence;
    }
  }
  return text.length > 100 ? "..." + text.slice(-100) : text;
}

function ThinkingIndicator() {
  const [isExpanded, setIsExpanded] = useState(false);

  const { head, commits, latestCommitHash } = useProjectStore();

  const currentCommit = head ? commits[head] : null;
  const selectedVariant = currentCommit
    ? currentCommit.variants[currentCommit.selectedVariantIndex]
    : null;
  const thinking = selectedVariant?.thinking || "";
  const code = selectedVariant?.code || "";
  const thinkingDuration = selectedVariant?.thinkingDuration;
  const isGenerating = selectedVariant?.status === "generating";

  // UI States:
  // - Waiting: isGenerating && !code && !thinking -> "AI is thinking..."
  // - Thinking: thinking && !code -> "AI Thinking" with content + pulsing
  // - Complete: thinking && code -> "AI thought for Xs" with content
  // - Hidden: !isGenerating && !thinking -> not rendered

  const isWaiting = isGenerating && !code && !thinking;
  const isThinkingInProgress = thinking.length > 0 && code.length === 0;
  const isThinkingComplete = thinking.length > 0 && code.length > 0;

  // Only show thinking for the latest commit, not historical ones
  const isLatestCommit = head === latestCommitHash;

  // Don't render if there's no thinking content and we're not in the waiting state
  if (!thinking && !isWaiting) {
    return null;
  }

  // Don't show thinking for historical commits
  if (!isLatestCommit) {
    return null;
  }

  // Determine header text
  let headerText = "AI Thinking";
  if (isWaiting) {
    headerText = "AI is thinking...";
  } else if (isThinkingComplete && thinkingDuration !== undefined) {
    headerText = `AI thought for ${thinkingDuration}s`;
  }

  const previewText = thinking ? getLastSentence(thinking) : "";

  const isActive = isWaiting || isThinkingInProgress;

  return (
    <div
      className={`mb-2 rounded-lg border ${
        isActive
          ? "working-indicator-bg border-brand-border"
          : "border-border bg-muted"
      }`}
    >
      <button
        type="button"
        onClick={() => setIsExpanded(!isExpanded)}
        aria-expanded={isExpanded}
        className="flex w-full items-center justify-between rounded-t-lg px-3 py-2 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <div className="flex items-center gap-2">
          {isExpanded ? (
            <BsChevronDown
              className="h-3 w-3 text-muted-foreground"
              aria-hidden="true"
            />
          ) : (
            <BsChevronRight
              className="h-3 w-3 text-muted-foreground"
              aria-hidden="true"
            />
          )}
          <span className="text-sm font-medium text-foreground">
            {headerText}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {(isWaiting || isThinkingInProgress) && (
            <span className="flex items-center gap-1.5">
              <span
                className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand"
                aria-hidden="true"
              />
              <span className="text-xs text-muted-foreground">
                {isWaiting ? "starting" : "reasoning"}
              </span>
            </span>
          )}
        </div>
      </button>

      {thinking && (
        <>
          {isExpanded ? (
            <div className="max-h-60 overflow-y-auto px-3 pb-3">
              <div className="prose prose-sm max-w-none text-sm leading-relaxed text-foreground dark:prose-invert">
                <ReactMarkdown>{thinking}</ReactMarkdown>
              </div>
            </div>
          ) : (
            // Only show preview when thinking is in progress, not when complete
            isThinkingInProgress && (
              <div className="px-3 pb-2">
                <p className="truncate text-sm text-muted-foreground">
                  {previewText}
                </p>
              </div>
            )
          )}
        </>
      )}
    </div>
  );
}

export default ThinkingIndicator;
