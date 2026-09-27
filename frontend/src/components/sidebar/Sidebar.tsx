import { useAppStore } from "../../store/app-store";
import { useProjectStore } from "../../store/project-store";
import { AppState } from "../../types";
import { Button } from "../ui/button";
import { useEffect, useRef, useState, useCallback } from "react";
import {
  LuMousePointerClick,
  LuRefreshCw,
  LuArrowUp,
  LuX,
  LuHistory,
} from "react-icons/lu";
import { toast } from "react-hot-toast";

import Variants from "../variants/Variants";
import UpdateImageUpload, { UpdateImagePreview } from "../UpdateImageUpload";
import AgentActivity from "../agent/AgentActivity";
import { formatCompletedGenerationDuration } from "../agent/generation-time";
import WorkingPulse from "../core/WorkingPulse";
import ImageLightbox from "../ImageLightbox";
import { Commit } from "../commits/types";
import { CodeGenerationModel } from "../../lib/models";
import DesignSystemSelector, {
  DesignSystemSelectorProps,
} from "../settings/DesignSystemSelector";

interface SidebarProps {
  doUpdate: (instruction: string) => void;
  regenerate: () => void;
  cancelCodeGeneration: () => void;
  onOpenVersions: () => void;
  designSystem: DesignSystemSelectorProps;
}

const MAX_UPDATE_IMAGES = 5;

function extractTagName(html: string): string {
  const match = html.match(/^<(\w+)/);
  return match ? match[1].toLowerCase() : "element";
}

function summarizeLatestChange(commit: Commit | null): string | null {
  if (!commit) return null;
  if (commit.type === "code_create") {
    // Cloned site pages carry their path; plain code imports do not.
    return commit.label ? `Cloned page ${commit.label}` : "Imported existing code.";
  }
  const text = commit.inputs.text.trim();
  if (text.length > 0) return text;

  if (commit.type === "ai_create") {
    return "Create";
  }

  if (commit.inputs.images.length > 1) {
    return `Updated with ${commit.inputs.images.length} reference images.`;
  }
  if (commit.inputs.images.length === 1) {
    return "Updated with one reference image.";
  }
  return "Updated code.";
}

function getSelectedElementTag(commit: Commit | null): string | null {
  if (!commit || commit.type === "code_create") return null;
  const html = commit.inputs.selectedElementHtml;
  if (!html) return null;
  return extractTagName(html);
}

function isSlowModel(model?: string): boolean {
  return (
    model === CodeGenerationModel.GEMINI_3_1_PRO_PREVIEW_HIGH ||
    model === CodeGenerationModel.GEMINI_3_1_PRO_PREVIEW_MEDIUM ||
    model === CodeGenerationModel.GPT_5_6_SOL_MAX
  );
}

function Sidebar({
  doUpdate,
  regenerate,
  cancelCodeGeneration,
  onOpenVersions,
  designSystem,
}: SidebarProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const middlePaneRef = useRef<HTMLDivElement>(null);
  const [isErrorExpanded, setIsErrorExpanded] = useState(false);
  const [isPromptExpanded, setIsPromptExpanded] = useState(false);
  const [isPromptClamped, setIsPromptClamped] = useState(false);
  const promptTextRef = useRef<HTMLParagraphElement>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [lightboxImage, setLightboxImage] = useState<string | null>(null);

  const {
    appState,
    updateInstruction,
    setUpdateInstruction,
    updateImages,
    setUpdateImages,
    inSelectAndEditMode,
    toggleInSelectAndEditMode,
    selectedElement,
    setSelectedElement,
  } = useAppStore();

  // Helper function to convert file to data URL
  const fileToDataURL = (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result as string);
      reader.onerror = (error) => reject(error);
      reader.readAsDataURL(file);
    });
  };

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);

      const files = Array.from(e.dataTransfer.files).filter(
        (file) => file.type === "image/png" || file.type === "image/jpeg"
      );

      if (files.length === 0) return;

      try {
        if (updateImages.length >= MAX_UPDATE_IMAGES) {
          toast.error(
            `You’ve reached the limit of ${MAX_UPDATE_IMAGES} reference images. Remove one to add another.`
          );
          return;
        }

        const remainingSlots = MAX_UPDATE_IMAGES - updateImages.length;
        let filesToAdd = files;
        if (filesToAdd.length > remainingSlots) {
          toast.error(
            `Only ${remainingSlots} more image${
              remainingSlots === 1 ? "" : "s"
            } will be added to stay within the ${MAX_UPDATE_IMAGES}-image limit.`
          );
          filesToAdd = filesToAdd.slice(0, remainingSlots);
        }

        const newImagePromises = filesToAdd.map((file) => fileToDataURL(file));
        const newImages = await Promise.all(newImagePromises);
        setUpdateImages([...updateImages, ...newImages]);
      } catch (error) {
        console.error("Error reading files:", error);
      }
    },
    [updateImages, setUpdateImages]
  );

  const { head, commits, latestCommitHash, setHead } = useProjectStore();

  const currentCommit = head ? commits[head] : null;
  const latestChangeSummary = summarizeLatestChange(currentCommit);
  const selectedElementTag = getSelectedElementTag(currentCommit);
  const latestChangeImages =
    currentCommit && currentCommit.type !== "code_create"
      ? currentCommit.inputs.images
      : [];
  const latestChangeVideos =
    currentCommit && currentCommit.type !== "code_create"
      ? currentCommit.inputs.videos ?? []
      : [];
  const selectedVariantIndex = currentCommit?.selectedVariantIndex ?? 0;
  const selectedVariant = currentCommit?.variants[selectedVariantIndex];
  const selectedVariantEvents = selectedVariant?.agentEvents ?? [];
  const showWorkingIndicator =
    appState === AppState.CODING &&
    selectedVariantEvents.length === 0 &&
    head === latestCommitHash;
  const requestStartMs =
    selectedVariant?.requestStartedAt ??
    (currentCommit?.dateCreated
      ? new Date(currentCommit.dateCreated).getTime()
      : undefined);
  const elapsedSeconds = requestStartMs
    ? Math.max(1, Math.round((nowMs - requestStartMs) / 1000))
    : undefined;
  const totalGenerationTime = formatCompletedGenerationDuration(
    selectedVariant?.status,
    requestStartMs,
    selectedVariant?.completedAt
  );

  const canRegenerate =
    currentCommit?.type === "ai_create" || currentCommit?.type === "ai_edit";
  const isViewingOlderVersion = head !== null && head !== latestCommitHash;

  // Compute version number for the current head
  const totalVersions = Object.keys(commits).length;
  const currentVersionNumber = (() => {
    if (!head) return null;
    const sorted = Object.values(commits).sort(
      (a, b) => new Date(a.dateCreated).getTime() - new Date(b.dateCreated).getTime()
    );
    const index = sorted.findIndex((c) => c.hash === head);
    return index !== -1 ? index + 1 : null;
  })();

  // Check if the currently selected variant is complete
  const isSelectedVariantComplete =
    head &&
    commits[head] &&
    commits[head].variants[commits[head].selectedVariantIndex].status ===
      "complete";

  // Check if the currently selected variant has an error
  const isSelectedVariantError =
    head &&
    commits[head] &&
    commits[head].variants[commits[head].selectedVariantIndex].status ===
      "error";

  // Get the error message from the selected variant
  const selectedVariantErrorMessage =
    head &&
    commits[head] &&
    commits[head].variants[commits[head].selectedVariantIndex].errorMessage;

  // Auto-resize textarea to fit content
  const autoResize = useCallback(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = "auto";
      textarea.style.height = textarea.scrollHeight + "px";
    }
  }, []);

  // Focus on the update instruction textarea when a variant is complete
  useEffect(() => {
    if (
      (appState === AppState.CODE_READY || isSelectedVariantComplete) &&
      textareaRef.current
    ) {
      const el = textareaRef.current;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    }
  }, [appState, isSelectedVariantComplete]);

  // Focus the textarea when an element is selected in the preview
  useEffect(() => {
    if (selectedElement && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [selectedElement]);

  // Reset textarea height when instruction changes externally (e.g., cleared after submit)
  useEffect(() => {
    autoResize();
  }, [updateInstruction, autoResize]);

  // Reset error expanded state when variant changes
  useEffect(() => {
    setIsErrorExpanded(false);
  }, [head, selectedVariantIndex]);

  // Reset prompt expanded state when commit changes and detect clamping
  useEffect(() => {
    setIsPromptExpanded(false);
  }, [head]);

  useEffect(() => {
    const el = promptTextRef.current;
    if (el) {
      setIsPromptClamped(el.scrollHeight > el.clientHeight);
    } else {
      setIsPromptClamped(false);
    }
  }, [latestChangeSummary, isPromptExpanded]);

  useEffect(() => {
    if (!middlePaneRef.current) return;
    requestAnimationFrame(() => {
      if (!middlePaneRef.current) return;
      middlePaneRef.current.scrollTop = middlePaneRef.current.scrollHeight;
    });
  }, [head, selectedVariantIndex]);

  useEffect(() => {
    if (appState !== AppState.CODING) return;
    const intervalId = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(intervalId);
  }, [appState]);


  return (
    <div className="flex h-full flex-col">
      <div className="shrink-0 border-b border-border bg-card px-4 py-2">
        <Variants />
      </div>

      {/* Prominent banner when viewing an older version */}
      {isViewingOlderVersion && currentVersionNumber !== null && (
        <div className="shrink-0 border-b border-border bg-muted px-4 py-2.5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex min-w-0 items-center gap-2">
              <LuHistory
                className="h-4 w-4 shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
              <p className="min-w-0 truncate text-sm font-medium text-foreground">
                Viewing v{currentVersionNumber} of {totalVersions}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                onClick={onOpenVersions}
                className="rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                All versions
              </button>
              <button
                type="button"
                onClick={() => latestCommitHash && setHead(latestCommitHash)}
                className="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-brand-foreground transition-colors hover:bg-brand-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Back to latest
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Scrollable content */}
      <div
        ref={middlePaneRef}
        className="sidebar-scrollbar-stable min-h-0 flex-1 overflow-y-auto px-5 pt-4"
      >
        {latestChangeSummary && (
          <div className="mb-4 flex flex-col items-end">
            <div className="inline-block max-w-[85%] rounded-2xl rounded-br-md border border-border bg-muted px-3.5 py-2.5">
              <p
                ref={promptTextRef}
                className={`whitespace-pre-wrap break-words text-[13px] leading-5 text-foreground ${
                  !isPromptExpanded ? "line-clamp-[10]" : ""
                }`}
              >
                {latestChangeSummary}
              </p>
              {selectedElementTag && (
                <div className="mt-1.5 flex items-center gap-1.5">
                  <LuMousePointerClick
                    className="h-3 w-3 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <span className="text-[11px] text-muted-foreground">
                    Selected:{" "}
                    <code className="rounded bg-background px-1 py-0.5 font-mono text-[10px]">
                      &lt;{selectedElementTag}&gt;
                    </code>
                  </span>
                </div>
              )}
              {(isPromptClamped || isPromptExpanded) && (
                <div className="mt-1.5 flex justify-end">
                  <button
                    type="button"
                    onClick={() => setIsPromptExpanded(!isPromptExpanded)}
                    className="rounded-full border border-border bg-card px-2 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {isPromptExpanded ? "less" : "more"}
                  </button>
                </div>
              )}
            </div>
              {latestChangeImages.length > 0 && (
                <div className="mt-2 flex flex-wrap justify-end gap-2">
                  {latestChangeImages.map((image, index) => (
                    <button
                      key={`${image.slice(0, 40)}-${index}`}
                      type="button"
                      onClick={() => setLightboxImage(image)}
                      className="shrink-0 cursor-zoom-in rounded-lg border border-border bg-card p-1 transition-colors hover:border-brand-border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <img
                        src={image}
                        alt={`Reference ${index + 1}`}
                        className="h-24 w-24 object-contain"
                        loading="lazy"
                      />
                    </button>
                  ))}
                </div>
              )}
              {latestChangeVideos.length > 0 && (
                <div className="mt-2 space-y-2">
                  {latestChangeVideos.map((video, index) => (
                    <video
                      key={`${video.slice(0, 40)}-${index}`}
                      src={video}
                      className="w-full rounded-lg border border-border"
                      controls
                      preload="metadata"
                    />
                  ))}
                </div>
              )}
          </div>
        )}

        {showWorkingIndicator && (
          <div className="working-indicator-bg mb-3 rounded-xl border border-border px-3 py-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm text-foreground">
                <WorkingPulse />
                <span>Working...</span>
              </div>
              <div className="text-xs font-medium tabular-nums text-muted-foreground">
                {elapsedSeconds ? `${elapsedSeconds}s` : "--"}
              </div>
            </div>
          </div>
        )}

        {currentCommit?.type === "ai_create" &&
          appState === AppState.CODING &&
          head === latestCommitHash &&
          !isSelectedVariantComplete &&
          !isSelectedVariantError &&
          isSlowModel(selectedVariant?.model) && (
          <div className="mb-3 rounded-lg border border-warning-border bg-warning-subtle px-3 py-2 text-xs leading-5 text-warning">
            Slow, high quality model. May take 5-10 mins on some images/videos.
          </div>
        )}

        {!isViewingOlderVersion && <AgentActivity />}

        {/* Retry any AI-generated version. A completed older version can be
            retried once no other request is running; the regenerated edit
            branches from that version's original parent. */}
        {canRegenerate &&
          (appState === AppState.CODE_READY ||
            (head === latestCommitHash &&
              (isSelectedVariantComplete || isSelectedVariantError))) && (
          <div className="mb-3 flex items-center justify-end gap-2">
            {totalGenerationTime && (
              <span
                className="text-[11px] font-medium tabular-nums text-muted-foreground"
                data-testid="total-generation-time"
              >
                {totalGenerationTime}
              </span>
            )}
            <button
              type="button"
              onClick={regenerate}
              className="flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <LuRefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Retry
            </button>
          </div>
        )}

        {/* Show cancel button when coding */}
        {appState === AppState.CODING && !isSelectedVariantComplete && (
          <div className="flex w-full">
            <Button
              onClick={cancelCodeGeneration}
              variant="outline"
              className="w-full"
            >
              Cancel all generations
            </Button>
          </div>
        )}

        {/* Show error message when selected option has an error */}
        {isSelectedVariantError && (
          <div className="mb-2 rounded-lg border border-danger-border bg-danger-subtle p-3">
            <div className="text-sm text-danger">
              <div className="mb-1 font-medium">
                This option failed to generate because
              </div>
              {selectedVariantErrorMessage && (
                <div className="mb-2">
                  <div className="break-words rounded border border-danger-border bg-danger/5 px-2 py-1 font-mono text-xs text-danger">
                    {selectedVariantErrorMessage.length > 200 && !isErrorExpanded
                      ? `${selectedVariantErrorMessage.slice(0, 200)}...`
                      : selectedVariantErrorMessage}
                  </div>
                  {selectedVariantErrorMessage.length > 200 && (
                    <button
                      type="button"
                      onClick={() => setIsErrorExpanded(!isErrorExpanded)}
                      className="mt-1 text-xs text-danger underline underline-offset-2"
                    >
                      {isErrorExpanded ? "Show less" : "Show more"}
                    </button>
                  )}
                </div>
              )}
              <div className="text-danger/90">
                {canRegenerate
                  ? "Click Retry to run this version's request again."
                  : "Switch to another option above to make updates."}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Pinned bottom: prompt box + option selector */}
      {(appState === AppState.CODE_READY || isSelectedVariantComplete) &&
        !isSelectedVariantError && (
          <div
            className="shrink-0 border-t border-border bg-card px-4 py-4"
            onDragEnter={() => setIsDragging(true)}
            onDragLeave={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node)) {
                setIsDragging(false);
              }
            }}
            onDragOver={(e) => e.preventDefault()}
            onDrop={handleDrop}
          >
            {/* Branching notice when editing an older version */}
            {isViewingOlderVersion && currentVersionNumber !== null && (
              <div className="mb-2 flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2">
                <LuHistory
                  className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                  aria-hidden="true"
                />
                <span className="text-xs leading-5 text-muted-foreground">
                  You're editing{" "}
                  <span className="font-medium text-foreground">
                    v{currentVersionNumber}
                  </span>{" "}
                  — updates will create a new version branching from it.
                </span>
              </div>
            )}

            {/* Select and edit indicator */}
            {inSelectAndEditMode && (
              <div className="mb-2">
                {selectedElement ? (
                  <div className="flex items-center justify-between rounded-lg border border-brand-border bg-brand-subtle px-3 py-2">
                    <div className="flex min-w-0 items-center gap-2">
                      <LuMousePointerClick
                        className="h-3.5 w-3.5 shrink-0 text-brand"
                        aria-hidden="true"
                      />
                      <span className="truncate text-sm text-foreground">
                        Selected:{" "}
                        <code className="rounded bg-card px-1.5 py-0.5 font-mono text-xs">
                          &lt;{selectedElement.tagName.toLowerCase()}&gt;
                        </code>
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={() => setSelectedElement(null)}
                      className="ml-3 shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      title="Clear selection"
                      aria-label="Clear selection"
                    >
                      <LuX className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center justify-between rounded-lg border border-border bg-muted px-3 py-2">
                    <div className="flex items-center gap-2">
                      <LuMousePointerClick
                        className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <span className="text-sm font-medium text-foreground">
                        Click an element to edit it
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={toggleInSelectAndEditMode}
                      className="ml-3 shrink-0 rounded text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      Exit
                    </button>
                  </div>
                )}
              </div>
            )}
            <div className="relative w-full overflow-hidden rounded-xl border border-input bg-background transition-colors focus-within:border-brand focus-within:ring-1 focus-within:ring-brand/30">
              <UpdateImagePreview
                updateImages={updateImages}
                setUpdateImages={setUpdateImages}
              />
              <textarea
                ref={textareaRef}
                placeholder={
                  inSelectAndEditMode && selectedElement
                    ? `Describe changes for the selected <${selectedElement.tagName.toLowerCase()}> element...`
                    : "Tell the AI what to change..."
                }
                onChange={(e) => {
                  setUpdateInstruction(e.target.value);
                  autoResize();
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    doUpdate(updateInstruction);
                  }
                }}
                value={updateInstruction}
                data-testid="update-input"
                rows={1}
                className="max-h-40 w-full resize-none border-0 bg-transparent px-3.5 pb-6 pt-3.5 text-[15px] leading-6 text-foreground placeholder:text-muted-foreground focus:outline-none"
              />
              <div className="flex items-center justify-between px-2.5 pb-2.5">
                <div className="flex items-center gap-1">
                  <UpdateImageUpload
                    updateImages={updateImages}
                    setUpdateImages={setUpdateImages}
                  />
                  <button
                    type="button"
                    onClick={toggleInSelectAndEditMode}
                    data-testid="select-edit-toggle-prompt"
                    className={`rounded-lg p-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      inSelectAndEditMode
                        ? "bg-brand-subtle text-brand"
                        : "text-muted-foreground hover:bg-accent hover:text-foreground"
                    }`}
                    title={
                      inSelectAndEditMode
                        ? "Exit selection mode"
                        : "Select an element in the preview to target your edit"
                    }
                  >
                    <LuMousePointerClick
                      className="h-[18px] w-[18px]"
                      aria-hidden="true"
                    />
                  </button>
                  <DesignSystemSelector {...designSystem} compact />
                </div>
                <button
                  type="button"
                  onClick={() => doUpdate(updateInstruction)}
                  disabled={!updateInstruction.trim()}
                  className={`rounded-lg p-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    updateInstruction.trim()
                      ? "bg-brand text-brand-foreground hover:bg-brand-muted"
                      : "cursor-not-allowed bg-muted text-muted-foreground"
                  }`}
                  title="Send"
                  aria-label="Send"
                >
                  <LuArrowUp
                    className="h-[18px] w-[18px]"
                    strokeWidth={2.5}
                    aria-hidden="true"
                  />
                </button>
              </div>

              {isDragging && (
                <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-xl border-2 border-dashed border-brand bg-background/90">
                  <p className="text-sm font-medium text-brand">
                    Drop images here
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

      <ImageLightbox
        image={lightboxImage}
        onClose={() => setLightboxImage(null)}
      />
    </div>
  );
}

export default Sidebar;
