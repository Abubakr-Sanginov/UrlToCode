import { useEffect, useRef, useState } from "react";
import { useProjectStore } from "../../store/project-store";
import { useAppStore } from "../../store/app-store";
import { AppState } from "../../types";
import {
  AgentEvent,
  AgentEventType,
  ToolImage,
} from "../commits/types";
import {
  BsChatDots,
  BsChevronDown,
  BsChevronRight,
  BsLightbulb,
  BsFileEarmarkPlus,
  BsPencilSquare,
  BsImage,
  BsScissors,
  BsFiles,
  BsBookmarkCheck,
  BsBoundingBox,
  BsCamera,
} from "react-icons/bs";
import ReactMarkdown from "react-markdown";
import { Light as SyntaxHighlighterBase } from "react-syntax-highlighter";
import html from "react-syntax-highlighter/dist/esm/languages/hljs/xml";
import { vs2015 } from "react-syntax-highlighter/dist/esm/styles/hljs";
import WorkingPulse from "../core/WorkingPulse";
import { groupCompletedAgentEvents } from "./activity-order";
import {
  formatDurationBetween,
  isTerminalVariantStatus,
} from "./generation-time";

SyntaxHighlighterBase.registerLanguage("html", html);
const SyntaxHighlighter =
  SyntaxHighlighterBase as unknown as React.ComponentType<
    Record<string, unknown>
  >;

function ExpandablePrompt({ prompt }: { prompt: string }) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [isClamped, setIsClamped] = useState(false);
  const promptRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    setIsExpanded(false);
  }, [prompt]);

  useEffect(() => {
    const element = promptRef.current;
    if (!element) return;

    const updateClampedState = () => {
      setIsClamped(element.scrollHeight > element.clientHeight + 1);
    };

    updateClampedState();
    if (typeof ResizeObserver === "undefined") return;

    const resizeObserver = new ResizeObserver(updateClampedState);
    resizeObserver.observe(element);
    return () => resizeObserver.disconnect();
  }, [prompt, isExpanded]);

  return (
    <div>
      <p
        ref={promptRef}
        className={`whitespace-pre-wrap break-words text-xs text-muted-foreground ${
          !isExpanded ? "line-clamp-4" : ""
        }`}
      >
        {prompt}
      </p>
      {(isClamped || isExpanded) && (
        <div className="mt-1 flex justify-end">
          <button
            type="button"
            onClick={() => setIsExpanded((previous) => !previous)}
            aria-expanded={isExpanded}
            className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-accent"
          >
            {isExpanded ? "Less" : "More"}
          </button>
        </div>
      )}
    </div>
  );
}

function CodePreviewBlock({ code, isGenerating }: { code: string; isGenerating: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isGenerating && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [code, isGenerating]);

  return (
    <div ref={containerRef} className="max-h-60 overflow-auto rounded-md">
      <SyntaxHighlighter
        language="html"
        style={vs2015}
        customStyle={{ margin: 0, padding: "0.5rem", fontSize: "0.75rem", borderRadius: "0.375rem" }}
        wrapLongLines
      >
        {code}
      </SyntaxHighlighter>
    </div>
  );
}

function formatDuration(startedAt?: number, endedAt?: number): string {
  return formatDurationBetween(startedAt, endedAt);
}

function formatElapsedSince(timestampMs: number | undefined, nowMs: number): string {
  return formatDurationBetween(timestampMs, nowMs);
}

function getArrayField(value: unknown, field: string): unknown[] | null {
  if (!value || typeof value !== "object") return null;
  const fieldValue = (value as Record<string, unknown>)[field];
  return Array.isArray(fieldValue) ? fieldValue : null;
}

function getRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object"
    ? (value as Record<string, unknown>)
    : null;
}

function getExtractedAssetPreviewUrl(asset: unknown): string | null {
  const assetRecord = getRecord(asset);
  if (!assetRecord) return null;
  if (typeof assetRecord.public_url === "string" && assetRecord.public_url) {
    return assetRecord.public_url;
  }
  if (typeof assetRecord.data_url === "string" && assetRecord.data_url) {
    return assetRecord.data_url;
  }
  return null;
}

function isSuccessfulExtractedAsset(asset: unknown): boolean {
  const assetRecord = getRecord(asset);
  if (!assetRecord) return false;
  const status =
    typeof assetRecord.status === "string" ? assetRecord.status : null;
  return Boolean(
    getExtractedAssetPreviewUrl(asset) &&
      status !== "missing" &&
      status !== "error"
  );
}

function getEventIcon(type: AgentEventType, toolName?: string) {
  if (type === "thinking") {
    return <BsLightbulb className="text-muted-foreground" />;
  }
  if (type === "assistant") {
    return <BsChatDots className="text-muted-foreground" />;
  }
  if (toolName === "create_file") {
    return <BsFileEarmarkPlus className="text-muted-foreground" />;
  }
  if (toolName === "edit_file") {
    return <BsPencilSquare className="text-muted-foreground" />;
  }
  if (toolName === "generate_images") {
    return <BsImage className="text-muted-foreground" />;
  }
  if (toolName === "remove_backgrounds") {
    return <BsScissors className="text-muted-foreground" />;
  }
  if (toolName === "edit_images") {
    return <BsImage className="text-muted-foreground" />;
  }
  if (toolName === "retrieve_option") {
    return <BsFiles className="text-muted-foreground" />;
  }
  if (toolName === "save_assets") {
    return <BsBookmarkCheck className="text-success" />;
  }
  if (toolName === "extract_assets") {
    return <BsBoundingBox className="text-muted-foreground" />;
  }
  if (toolName === "screenshot_preview") {
    return <BsCamera className="text-muted-foreground" />;
  }
  return <BsFileEarmarkPlus className="text-muted-foreground" />;
}

function getEventTitle(event: AgentEvent): string {
  if (event.type === "thinking") {
    if (event.status === "running") return "Thinking";
    const duration = formatDuration(event.startedAt, event.endedAt);
    return duration ? `Thought for ${duration}` : "Thought";
  }
  if (event.type === "assistant") {
    return "Assistant response";
  }
  if (event.type === "tool") {
    if (event.toolName === "create_file") {
      return event.status === "running" ? "Creating file" : "Created file";
    }
    if (event.toolName === "edit_file") {
      return event.status === "running" ? "Editing file" : "Edited file";
    }
    if (event.toolName === "generate_images") {
      const input = event.input;
      const output = event.output;
      const count = output?.images?.length || input?.count || 0;
      if (event.status === "running") {
        return count ? `Generating ${count} image${count !== 1 ? "s" : ""}` : "Generating images";
      }
      return count ? `Generated ${count} image${count !== 1 ? "s" : ""}` : "Generated images";
    }
    if (event.toolName === "remove_backgrounds") {
      const rbInput = event.input;
      const rbOutput = event.output;
      const rbCount = rbOutput?.images?.length || rbInput?.image_urls?.length || 0;
      if (event.status === "running") {
        return rbCount > 1 ? `Removing ${rbCount} backgrounds` : "Removing background";
      }
      return rbCount > 1 ? `Removed ${rbCount} backgrounds` : "Background removed";
    }
    if (event.toolName === "edit_images") {
      const editInput = event.input;
      const editOutput = event.output;
      const editCount =
        editOutput?.images?.length || editInput?.edits?.length || 0;
      if (event.status === "running") {
        return editCount
          ? `Editing ${editCount} image${editCount !== 1 ? "s" : ""}`
          : "Editing images";
      }
      return editCount
        ? `Edited ${editCount} image${editCount !== 1 ? "s" : ""}`
        : "Edited images";
    }
    if (event.toolName === "retrieve_option") {
      return event.status === "running"
        ? "Retrieving option"
        : "Retrieved option";
    }
    if (event.toolName === "save_assets") {
      const saveInput = event.input;
      const saveOutput = event.output;
      const saveCount = saveOutput?.images?.length || saveInput?.asset_ids?.length || 0;
      if (event.status === "running") {
        return saveCount > 1 ? `Saving ${saveCount} assets` : "Saving asset";
      }
      return saveCount > 1 ? `Saved ${saveCount} assets` : "Saved asset";
    }
    if (event.toolName === "extract_assets") {
      const extractInputDescriptions = getArrayField(event.input, "asset_descriptions");
      const extractOutputAssets = getArrayField(event.output, "assets");
      const requestedCount = extractInputDescriptions?.length || 0;
      const successfulCount =
        extractOutputAssets?.filter(isSuccessfulExtractedAsset).length || 0;
      const extractCount =
        extractOutputAssets?.length || extractInputDescriptions?.length || 0;
      if (event.status === "running") {
        return extractCount > 1
          ? `Extracting ${extractCount} assets`
          : "Extracting asset";
      }
      if (
        extractOutputAssets &&
        requestedCount > 0 &&
        successfulCount < requestedCount
      ) {
        return successfulCount > 0
          ? `Extracted ${successfulCount} of ${requestedCount} assets`
          : "Could not extract assets";
      }
      return extractCount > 1
        ? `Extracted ${extractCount} assets`
        : "Extracted asset";
    }
    if (event.toolName === "screenshot_preview") {
      return event.status === "running"
        ? "Screenshotting preview"
        : "Screenshotted preview";
    }
    return event.status === "running" ? "Running tool" : "Tool completed";
  }
  return "Activity";
}


function renderToolDetails(event: AgentEvent, variantCode?: string) {
  if (!event.input && !event.output) return null;

  const renderJson = (data: unknown) => {
    if (!data) return null;
    let json = "";
    try {
      json = JSON.stringify(data, null, 2);
    } catch {
      json = String(data);
    }
    if (json.length > 900) {
      json = json.slice(0, 900) + "...";
    }
    return (
      <pre className="mt-2 rounded-md bg-muted p-2 text-xs text-foreground overflow-x-auto">
        {json}
      </pre>
    );
  };

  const output = event.output;
  const input = event.input;
  const hasError = Boolean(output?.error);
  const images =
    output && Array.isArray(output.images) ? output.images : null;
  const edits =
    output && Array.isArray(output.edits) ? output.edits : null;
  const extractedAssets =
    output && Array.isArray(output.assets) ? (output.assets as Array<unknown>) : null;
  const successfulExtractedAssets =
    extractedAssets?.filter(isSuccessfulExtractedAsset) || null;
  const visibleExtractedAssets =
    hasError && successfulExtractedAssets
      ? successfulExtractedAssets
      : extractedAssets;
  const screenshotPreviews =
    output && Array.isArray(output.screenshots)
      ? (output.screenshots as Array<unknown>)
      : [];

  return (
    <div className="text-sm text-foreground">
      {hasError && (
        <div className="rounded-md border border-danger-border bg-danger-subtle p-3">
          <div className="text-xs uppercase tracking-wide text-danger">Error</div>
          <div className="mt-1 text-sm text-danger">
            {output?.error}
          </div>
          {event.input && (
            <div className="mt-2">
              <div className="text-xs uppercase tracking-wide text-danger">
                Input
              </div>
              {renderJson(event.input)}
            </div>
          )}
        </div>
      )}
      {event.toolName === "create_file" && !hasError && variantCode && (
        <CodePreviewBlock code={variantCode} isGenerating={event.status === "running"} />
      )}

      {event.toolName === "edit_file" && edits && !hasError && (
        <div className="space-y-2">
          {edits.map((edit, index) => (
            <div
              key={`${edit.old_text}-${index}`}
              className="rounded-md border border-border bg-card p-3"
            >
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Edit {index + 1}
              </div>
              <div className="mt-2 grid gap-2">
                <div>
                  <div className="text-xs text-muted-foreground">Old</div>
                  <div className="mt-1 rounded bg-danger-subtle p-2 text-xs font-mono text-danger break-all">
                    {edit.old_text}
                  </div>
                </div>
                <div>
                  <div className="text-xs text-muted-foreground">New</div>
                  <div className="mt-1 rounded bg-success-subtle p-2 text-xs font-mono text-success break-all">
                    {edit.new_text}
                  </div>
                </div>
              </div>
              {edit.replaced !== undefined && (
                <div className="mt-2 text-xs text-muted-foreground">
                  Replaced {edit.replaced} time{edit.replaced === 1 ? "" : "s"}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {event.toolName === "generate_images" && !hasError && (
        <div>
          {/* While running: show prompts with dividers */}
          {event.status === "running" && input?.prompts && Array.isArray(input.prompts) && (
            <div className="divide-y divide-border">
              {input.prompts.map((prompt: string, index: number) => (
                <div key={index} className="text-xs text-muted-foreground py-1.5">
                  {prompt}
                </div>
              ))}
            </div>
          )}
          {/* After complete: 50/50 image left, prompt right */}
          {event.status !== "running" && images && (
            <div className="divide-y divide-border">
              {images.map((item, index) => (
                <div key={`${item.prompt}-${index}`} className="flex gap-3 py-2">
                  <div className="w-1/2 shrink-0">
                    {item.url ? (
                      <img
                        src={item.url}
                        alt={item.prompt || `Generated image ${index + 1}`}
                        className="w-full rounded object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="aspect-square rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
                        Failed
                      </div>
                    )}
                  </div>
                  <div className="w-1/2 text-xs text-muted-foreground self-center">
                    {item.prompt}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {event.toolName === "remove_backgrounds" && !hasError && (
        <div>
          {/* While running: show the source images */}
          {event.status === "running" && input?.image_urls && Array.isArray(input.image_urls) && (
            <div className="divide-y divide-border">
              {input.image_urls.map((url: string, index: number) => (
                <div key={index} className="py-2">
                  <img
                    src={url}
                    alt={`Original image ${index + 1}`}
                    className="w-full rounded object-cover"
                    loading="lazy"
                  />
                </div>
              ))}
            </div>
          )}
          {/* After complete: before/after side by side for each image */}
          {event.status !== "running" && output?.images && Array.isArray(output.images) && (
            <div className="divide-y divide-border">
              {output.images.map((item: ToolImage, index: number) => (
                <div key={`${item.image_url}-${index}`} className="flex gap-2 py-2">
                  <div className="w-1/2">
                    <div className="text-xs text-muted-foreground mb-1">Before</div>
                    <img
                      src={item.image_url}
                      alt={`Original image ${index + 1}`}
                      className="w-full rounded object-cover"
                      loading="lazy"
                    />
                  </div>
                  <div className="w-1/2">
                    <div className="text-xs text-muted-foreground mb-1">After</div>
                    {item.result_url ? (
                      <div className="relative">
                        <div
                          className="absolute inset-0 rounded"
                          style={{
                            backgroundImage:
                              "linear-gradient(45deg, #e5e7eb 25%, transparent 25%), linear-gradient(-45deg, #e5e7eb 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #e5e7eb 75%), linear-gradient(-45deg, transparent 75%, #e5e7eb 75%)",
                            backgroundSize: "10px 10px",
                            backgroundPosition: "0 0, 0 5px, 5px -5px, -5px 0px",
                          }}
                        />
                        <img
                          src={item.result_url}
                          alt="Background removed"
                          className="relative w-full rounded"
                          loading="lazy"
                        />
                      </div>
                    ) : (
                      <div className="aspect-square rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
                        Failed
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {event.toolName === "edit_images" && !hasError && (
        <div>
          {event.status === "running" &&
            input?.edits &&
            Array.isArray(input.edits) && (
              <div className="divide-y divide-border">
                {input.edits.map((rawItem: unknown, index: number) => {
                  const item = getRecord(rawItem);
                  const imageUrls = (getArrayField(item, "image_urls") || []).filter(
                    (url): url is string => typeof url === "string"
                  );
                  const prompt =
                    typeof item?.prompt === "string" ? item.prompt : null;
                  const aspectRatio =
                    typeof item?.aspect_ratio === "string"
                      ? item.aspect_ratio
                      : "match input";
                  return (
                    <div key={`${prompt || "edit"}-${index}`} className="py-3">
                      <div className="flex items-center justify-between gap-2">
                        <div className="text-xs font-medium text-foreground">
                          Edit {index + 1}
                        </div>
                        <div className="rounded-full bg-brand-subtle px-2 py-0.5 text-[10px] text-brand">
                          {aspectRatio}
                        </div>
                      </div>
                      {prompt && (
                        <div className="mt-2">
                          <ExpandablePrompt prompt={prompt} />
                        </div>
                      )}
                      <div className="mt-3 grid grid-cols-3 gap-2">
                        {imageUrls.map((url: string, imageIndex: number) => (
                          <div key={`${url}-${imageIndex}`}>
                            <div className="mb-1 text-[10px] text-muted-foreground">
                              {imageIndex === 0
                                ? "Main image"
                                : `Reference ${imageIndex}`}
                            </div>
                            <img
                              src={url}
                              alt={
                                imageIndex === 0
                                  ? `Main image for edit ${index + 1}`
                                  : `Reference ${imageIndex} for edit ${index + 1}`
                              }
                              className="aspect-square w-full rounded object-cover bg-muted"
                              loading="lazy"
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          {event.status !== "running" && images && (
            <div className="divide-y divide-border">
              {images.map((item, index) => {
                const imageUrls = Array.isArray(item?.image_urls)
                  ? item.image_urls
                  : [];
                const prompt =
                  typeof item?.prompt === "string" ? item.prompt : null;
                const succeeded =
                  item?.status === "ok" && typeof item?.result_url === "string";
                return (
                  <div key={`${prompt || "edit"}-${index}`} className="space-y-3 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-xs font-medium text-foreground">
                        Edit {index + 1}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                          {item?.aspect_ratio || "match input"}
                        </span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] ${
                            succeeded
                              ? "bg-success-subtle text-success"
                              : "bg-danger-subtle text-danger"
                          }`}
                        >
                          {succeeded ? "Complete" : "Failed"}
                        </span>
                      </div>
                    </div>
                    {prompt && <ExpandablePrompt prompt={prompt} />}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <div className="mb-1 text-xs text-muted-foreground">
                          Before
                        </div>
                        {imageUrls[0] ? (
                          <img
                            src={imageUrls[0]}
                            alt={`Main image for edit ${index + 1}`}
                            className="aspect-square w-full rounded object-contain bg-muted"
                            loading="lazy"
                          />
                        ) : (
                          <div className="aspect-square rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
                            Missing
                          </div>
                        )}
                      </div>
                      <div>
                        <div className="mb-1 text-xs text-muted-foreground">
                          After
                        </div>
                        {succeeded ? (
                          <img
                            src={item.result_url}
                            alt={`Edited image ${index + 1}`}
                            className="aspect-square w-full rounded object-contain bg-muted"
                            loading="lazy"
                          />
                        ) : (
                          <div className="aspect-square rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
                            Failed
                          </div>
                        )}
                      </div>
                    </div>
                    {imageUrls.length > 1 && (
                      <div>
                        <div className="mb-1 text-xs text-muted-foreground">
                          Reference images
                        </div>
                        <div className="grid grid-cols-3 gap-2">
                          {imageUrls
                            .slice(1)
                            .map((url: string, referenceIndex: number) => (
                              <img
                                key={`${url}-${referenceIndex}`}
                                src={url}
                                alt={`Reference ${referenceIndex + 1} for edit ${index + 1}`}
                                className="aspect-square w-full rounded object-cover bg-muted"
                                loading="lazy"
                              />
                            ))}
                        </div>
                      </div>
                    )}
                    {item?.error && (
                      <div className="rounded bg-danger-subtle p-2 text-xs text-danger">
                        {item.error}
                      </div>
                    )}
                    {succeeded && (
                      <div>
                        <div className="text-xs text-muted-foreground">
                          Result URL
                        </div>
                        <div className="mt-1 break-all rounded bg-muted p-2 font-mono text-xs text-muted-foreground">
                          {item.result_url}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {event.toolName === "save_assets" && !hasError && (
        <div className="space-y-3">
          {event.status === "running" && input?.asset_ids && Array.isArray(input.asset_ids) && (
            <div className="divide-y divide-border">
              {input.asset_ids.map((assetId: string, index: number) => (
                <div key={`${assetId}-${index}`} className="py-2">
                  <div className="text-xs text-muted-foreground mb-1">
                    Asset ID
                  </div>
                  <div className="break-all rounded bg-muted p-2 font-mono text-xs text-muted-foreground">
                    {assetId}
                  </div>
                </div>
              ))}
            </div>
          )}
          {event.status !== "running" && output?.images && Array.isArray(output.images) && (
            <div className="divide-y divide-border">
              {output.images.map((item: ToolImage, index: number) => (
                <div key={`${item.asset_id}-${index}`} className="flex gap-3 py-2">
                  <div className="w-1/2">
                    <div className="text-xs text-muted-foreground mb-1">
                      Saved asset
                    </div>
                    {item.public_url ? (
                      <img
                        src={item.public_url}
                        alt={`Saved uploaded asset ${index + 1}`}
                        className="w-full rounded object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="aspect-square rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
                        Failed
                      </div>
                    )}
                  </div>
                  <div className="w-1/2 self-center">
                    <div className="text-xs text-muted-foreground">
                      Permanent URL
                    </div>
                    <div className="mt-1 break-all rounded bg-muted p-2 font-mono text-xs text-muted-foreground">
                      {item.public_url}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {event.toolName === "extract_assets" &&
        (!hasError || Boolean(visibleExtractedAssets?.length)) && (
          <div>
            {!hasError &&
              event.status === "running" &&
              input?.asset_descriptions &&
              Array.isArray(input.asset_descriptions) && (
                <div className="divide-y divide-border">
                  {input.asset_descriptions.map((description: string, index: number) => (
                    <div key={`${description}-${index}`} className="py-2">
                      <div className="text-xs text-muted-foreground mb-1">
                        Asset {index + 1}
                      </div>
                      <div className="text-xs text-muted-foreground">
                        {description}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            {event.status !== "running" && visibleExtractedAssets && (
              <div className="divide-y divide-border">
                {visibleExtractedAssets.map((asset, index) => {
                  const assetRecord =
                    asset && typeof asset === "object"
                      ? (asset as Record<string, unknown>)
                      : {};
                  const description =
                    typeof assetRecord.description === "string"
                      ? assetRecord.description
                      : `Asset ${index + 1}`;
                  const publicUrl =
                    typeof assetRecord.public_url === "string"
                      ? assetRecord.public_url
                      : null;
                  const previewUrl =
                    publicUrl ||
                    (typeof assetRecord.data_url === "string"
                      ? assetRecord.data_url
                      : null);
                  const boxText = Array.isArray(assetRecord.box_2d)
                    ? assetRecord.box_2d.join(", ")
                    : "No box";
                  const statusLabel =
                    typeof assetRecord.status === "string"
                      ? assetRecord.status
                      : previewUrl
                        ? "ok"
                        : "missing";
                  return (
                    <div key={`${description}-${index}`} className="flex gap-3 py-2">
                      <div className="w-1/2 shrink-0">
                        <div className="text-xs text-muted-foreground mb-1">
                          Extracted crop
                        </div>
                        {previewUrl ? (
                          <img
                            src={previewUrl}
                            alt={description}
                            className="max-h-48 w-full rounded object-contain bg-muted"
                            loading="lazy"
                          />
                        ) : (
                          <div className="aspect-square rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
                            Missing
                          </div>
                        )}
                      </div>
                      <div className="w-1/2 self-center space-y-2">
                        <div>
                          <div className="text-xs text-muted-foreground">
                            Requested asset
                          </div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            {description}
                          </div>
                        </div>
                        {publicUrl && (
                          <div>
                            <div className="text-xs text-muted-foreground">
                              Public URL
                            </div>
                            <div className="mt-1 break-all rounded bg-muted p-2 font-mono text-xs text-muted-foreground">
                              {publicUrl}
                            </div>
                          </div>
                        )}
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          <div>
                            <div className="text-muted-foreground">Status</div>
                            <div className="mt-1 font-mono text-muted-foreground">
                              {statusLabel}
                            </div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">
                              Source image
                            </div>
                            <div className="mt-1 font-mono text-muted-foreground">
                              {String(assetRecord.image_index ?? "-")}
                            </div>
                          </div>
                        </div>
                        <div>
                          <div className="text-xs text-muted-foreground">
                            Bounding box
                          </div>
                          <div className="mt-1 break-all rounded bg-muted p-2 font-mono text-xs text-muted-foreground">
                            [{boxText}]
                          </div>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}

      {event.toolName === "screenshot_preview" && !hasError && (
        <div>
          {event.status === "running" && (
            <div className="text-xs text-muted-foreground py-1.5">
              Rendering desktop and mobile previews...
            </div>
          )}
          {event.status !== "running" && (
            <div className="grid gap-3 py-2 sm:grid-cols-2">
              {(["desktop", "mobile"] as const).map((viewport) => {
                const screenshot = screenshotPreviews.find((item) => {
                  const itemRecord = getRecord(item);
                  return itemRecord?.viewport === viewport;
                });
                const screenshotRecord = getRecord(screenshot);
                const imageUrl =
                  typeof screenshotRecord?.image_url === "string"
                    ? screenshotRecord.image_url
                    : null;
                return (
                  <div key={viewport}>
                    <div className="text-xs text-muted-foreground mb-1 capitalize">
                      {viewport}
                    </div>
                    {imageUrl ? (
                      <div className="max-h-96 overflow-y-auto rounded border border-border">
                        <img
                          src={imageUrl}
                          alt={`Screenshot of the generated ${viewport} preview`}
                          className="w-full"
                          loading="lazy"
                        />
                      </div>
                    ) : (
                      <div className="aspect-square rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
                        Missing
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {!event.toolName && !hasError && (
        <>
          {event.input && (
            <div>
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Input
              </div>
              {renderJson(event.input)}
            </div>
          )}
          {event.output && (
            <div className="mt-3">
              <div className="text-xs uppercase tracking-wide text-muted-foreground">
                Output
              </div>
              {renderJson(event.output)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function AgentEventCard({
  event,
  autoExpand,
  variantCode,
}: {
  event: AgentEvent;
  autoExpand?: boolean;
  variantCode?: string;
}) {
  const [expanded, setExpanded] = useState(Boolean(autoExpand));

  useEffect(() => {
    if (autoExpand) {
      setExpanded(true);
    }
  }, [autoExpand]);

  const isExpanded =
    (event.type !== "thinking" && event.status === "running") || expanded;

  if (event.type === "assistant") {
    if (!event.content) return null;
    return (
      <div className="py-1 text-sm text-foreground prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown
          components={{
            img: ({ ...props }) => (
              <div className="my-2 flex justify-start max-w-full">
                <img
                  {...props}
                  className="max-h-60 max-w-full object-contain rounded-lg border border-border"
                  loading="lazy"
                />
              </div>
            ),
          }}
        >
          {event.content}
        </ReactMarkdown>
      </div>
    );
  }

  return (
    <div>
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full flex items-center gap-2 py-1.5 text-left text-muted-foreground hover:text-foreground"
      >
        {getEventIcon(event.type, event.toolName)}
        <span className={`text-sm flex-1 ${event.status === "running" ? "active-step-shimmer" : ""}`}>
          {getEventTitle(event)}
        </span>
        {isExpanded ? (
          <BsChevronDown className="text-xs shrink-0" />
        ) : (
          <BsChevronRight className="text-xs shrink-0" />
        )}
      </button>
      {isExpanded && (
        <div className="pb-2">
          {event.type === "thinking" && event.content && (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown
                components={{
                  img: ({ ...props }) => (
                    <div className="my-2 flex justify-start max-w-full">
                      <img
                        {...props}
                        className="max-h-60 max-w-full object-contain rounded-lg border border-border"
                        loading="lazy"
                      />
                    </div>
                  ),
                }}
              >
                {event.content}
              </ReactMarkdown>
            </div>
          )}
          {event.type === "tool" && renderToolDetails(event, variantCode)}
        </div>
      )}
    </div>
  );
}

function AgentActivity() {
  const { head, commits, latestCommitHash } = useProjectStore();
  const [expandedStepGroups, setExpandedStepGroups] = useState<
    Record<string, boolean>
  >({});
  const [nowMs, setNowMs] = useState(() => Date.now());
  const appState = useAppStore((s) => s.appState);

  useEffect(() => {
    if (appState !== AppState.CODING) return;
    const intervalId = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(intervalId);
  }, [appState]);

  const currentCommit = head ? commits[head] : null;
  const selectedVariant = currentCommit
    ? currentCommit.variants[currentCommit.selectedVariantIndex]
    : null;
  const selectedVariantStatus = selectedVariant?.status;
  const variantUiKey =
    currentCommit ? `${currentCommit.hash}:${currentCommit.selectedVariantIndex}` : "";

  const variantCode = selectedVariant?.code || "";
  const events = selectedVariant?.agentEvents || [];
  const lastAssistantId = [...events]
    .reverse()
    .find((event) => event.type === "assistant")?.id;
  const requestStartMs =
    selectedVariant?.requestStartedAt ??
    (currentCommit?.dateCreated
      ? new Date(currentCommit.dateCreated).getTime()
      : undefined);

  const isDone = isTerminalVariantStatus(selectedVariantStatus);
  const isLatestCommit = head === latestCommitHash;
  if (!isLatestCommit || events.length === 0) {
    return null;
  }

  const runningDuration = formatElapsedSince(requestStartMs, nowMs);
  const completedEventGroups = groupCompletedAgentEvents(events);

  return (
    <div className="space-y-1 mb-3">
      {isDone ? (
        <>
          {completedEventGroups.map((group) => {
            if (group.type === "assistant") {
              return (
                <AgentEventCard
                  key={group.event.id}
                  event={group.event}
                  autoExpand={group.event.id === lastAssistantId}
                />
              );
            }

            const firstStep = group.events[0];
            const lastStep = group.events[group.events.length - 1];
            const groupUiKey = `${variantUiKey}:${firstStep.id}`;
            const isExpanded = Boolean(expandedStepGroups[groupUiKey]);
            const groupDuration = formatDuration(
              firstStep.startedAt,
              lastStep.endedAt
            );

            return (
              <div key={groupUiKey}>
                <button
                  onClick={() =>
                    setExpandedStepGroups((previous) => ({
                      ...previous,
                      [groupUiKey]: !previous[groupUiKey],
                    }))
                  }
                  className="w-full flex items-center gap-2 rounded-xl border border-border bg-card px-3 py-2 text-left"
                >
                  {isExpanded ? (
                    <BsChevronDown className="text-muted-foreground text-xs" />
                  ) : (
                    <BsChevronRight className="text-muted-foreground text-xs" />
                  )}
                  <span className="text-xs text-muted-foreground">
                    Worked through {group.events.length} step
                    {group.events.length !== 1 ? "s" : ""}
                    {groupDuration ? ` in ${groupDuration}` : ""}
                  </span>
                </button>
                {isExpanded && (
                  <div className="space-y-1">
                    {group.events.map((event) => (
                      <AgentEventCard
                        key={event.id}
                        event={event}
                        variantCode={
                          event.toolName === "create_file"
                            ? variantCode
                            : undefined
                        }
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </>
      ) : (
        <>
          <div className="working-indicator-bg flex items-center justify-between rounded-xl border border-border px-3 py-2">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <WorkingPulse />
              <span>Working...</span>
            </div>
            <div className="text-xs font-semibold text-foreground">
              Time so far {runningDuration || "--"}
            </div>
          </div>
          {events.map((event) => (
            <AgentEventCard
              key={event.id}
              event={event}
              autoExpand={event.type === "assistant" && event.id === lastAssistantId}
              variantCode={event.toolName === "create_file" ? variantCode : undefined}
            />
          ))}
        </>
      )}
    </div>
  );
}

export default AgentActivity;
