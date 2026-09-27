import { useId, useState } from "react";
import {
  LuLoader2,
  LuCheck,
  LuX,
  LuChevronDown,
  LuChevronUp,
  LuAlertTriangle,
  LuRotateCcw,
  LuArrowRight,
} from "react-icons/lu";
import { toast } from "react-hot-toast";
import { Input } from "../ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../ui/select";
import { Stack, STACK_DESCRIPTIONS } from "../../lib/stacks";
import { Settings } from "../../types";
import {
  CrawlRunState,
  MAX_CRAWL_DEPTH,
  MAX_CRAWL_PAGES,
  StartCrawlParams,
} from "../../hooks/useUrlToCode";
import { useElapsedTime } from "../../hooks/useElapsedTime";

interface Props {
  stack: Stack;
  setStack: (stack: Stack) => void;
  startCrawl: (params: StartCrawlParams) => void;
  cancelCrawl: () => void;
  state: CrawlRunState;
  settings: Settings;
}

function stackLabel(stack: Stack): string {
  return STACK_DESCRIPTIONS[stack].components.join(" + ");
}

function hasAnyProvider(settings: Settings): boolean {
  return Boolean(
    settings.customProviderBaseUrl ||
      settings.openRouterApiKey ||
      settings.anthropicApiKey ||
      settings.openAiApiKey ||
      settings.geminiApiKey
  );
}

function clampNumber(raw: string, fallback: number, min: number, max: number) {
  const parsed = Number.parseInt(raw, 10);
  if (Number.isNaN(parsed)) return fallback;
  return Math.min(Math.max(parsed, min), max);
}

const FIELD_LABEL =
  "block text-xs font-medium text-muted-foreground";
const NUMBER_INPUT =
  "h-8 w-16 rounded-md border border-input bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-card disabled:opacity-50";

function UrlToCodePane({
  stack,
  setStack,
  startCrawl,
  cancelCrawl,
  state,
  settings,
}: Props) {
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(10);
  const [maxDepth, setMaxDepth] = useState(2);
  const [generateDatabase, setGenerateDatabase] = useState(false);
  const [generateAuth, setGenerateAuth] = useState(false);
  // No folder connected by default: everything lands in one HTML file.
  const [singleFile, setSingleFile] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const ids = useId();
  const urlId = `${ids}-url`;
  const stackId = `${ids}-stack`;
  const maxPagesId = `${ids}-max-pages`;
  const maxDepthId = `${ids}-max-depth`;
  const advancedId = `${ids}-advanced`;

  const { isLoading, phase } = state;
  const elapsed = useElapsedTime(state.startedAt, isLoading);
  const providerConfigured = hasAnyProvider(settings);

  const totalPages =
    state.currentPage?.totalPages ?? state.crawlComplete?.pagesFound ?? 0;
  const donePages = state.completedPages.length + state.failedPages.length;
  const percent =
    totalPages > 0 ? Math.round((donePages / totalPages) * 100) : 0;

  function handleStart() {
    const trimmed = url.trim();
    if (!trimmed) {
      toast.error("Enter a website URL first");
      return;
    }
    if (/\s/.test(trimmed)) {
      toast.error("That does not look like a valid URL");
      return;
    }

    const finalUrl = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
    try {
      // Throws on anything the backend could not fetch either.
      new URL(finalUrl);
    } catch {
      toast.error("That does not look like a valid URL");
      return;
    }

    startCrawl({
      url: finalUrl,
      stack,
      maxPages,
      maxDepth,
      generateDatabase,
      generateAuth,
      singleFile,
      settings,
    });
  }

  const statusLine = state.currentPage
    ? `Generating ${state.currentPage.path} (${state.currentPage.pageIndex + 1}/${state.currentPage.totalPages})`
    : state.status;

  return (
    <div className="flex w-full flex-col gap-3">
      <div className="relative overflow-hidden rounded-2xl border border-border bg-card shadow-raised">
        {/* Brand accent line along the top edge */}
        <div
          aria-hidden="true"
          className="h-0.5 w-full bg-gradient-to-r from-brand/0 via-brand/70 to-brand/0"
        />

        <div className="space-y-4 px-5 py-5">
          {!providerConfigured && (
            <div className="flex items-start gap-2 rounded-lg border border-warning-border bg-warning-subtle px-3 py-2">
              <LuAlertTriangle
                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning"
                aria-hidden="true"
              />
              <p className="text-xs leading-5 text-warning">
                No model provider configured. Add an API key in Settings, or the
                run will fail.
              </p>
            </div>
          )}

          <div>
            <label htmlFor={urlId} className={FIELD_LABEL}>
              Website URL
            </label>
            <Input
              id={urlId}
              type="url"
              inputMode="url"
              autoComplete="url"
              placeholder="https://example.com"
              onChange={(e) => setUrl(e.target.value)}
              value={url}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isLoading) {
                  e.preventDefault();
                  handleStart();
                }
              }}
              className="mt-1.5 h-11 w-full text-sm"
              disabled={isLoading}
              data-testid="clone-url-input"
            />
          </div>

          <div className="flex items-center gap-3">
            <label htmlFor={stackId} className={FIELD_LABEL}>
              Stack
            </label>
            <Select
              value={stack}
              onValueChange={(value) => setStack(value as Stack)}
              disabled={isLoading}
            >
              <SelectTrigger
                id={stackId}
                className="h-8 w-auto gap-2 text-xs font-medium"
                data-testid="stack-select"
              >
                <SelectValue placeholder="Select a stack" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  {Object.values(Stack).map((s) => (
                    <SelectItem key={s} value={s}>
                      <span className="text-sm">{stackLabel(s)}</span>
                    </SelectItem>
                  ))}
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>

          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced((prev) => !prev)}
              aria-expanded={showAdvanced}
              aria-controls={advancedId}
              className="-mx-1 flex items-center gap-1.5 rounded px-1 py-0.5 text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {showAdvanced ? (
                <LuChevronUp className="h-3 w-3" aria-hidden="true" />
              ) : (
                <LuChevronDown className="h-3 w-3" aria-hidden="true" />
              )}
              Advanced options
            </button>

            {showAdvanced && (
              <div
                id={advancedId}
                className="mt-2.5 space-y-3 rounded-lg border border-border bg-muted/40 p-3"
              >
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                  <div className="flex items-center gap-2">
                    <label htmlFor={maxPagesId} className={FIELD_LABEL}>
                      Max pages
                    </label>
                    <input
                      id={maxPagesId}
                      type="number"
                      min={1}
                      max={MAX_CRAWL_PAGES}
                      value={maxPages}
                      onChange={(e) =>
                        setMaxPages(
                          clampNumber(e.target.value, maxPages, 1, MAX_CRAWL_PAGES)
                        )
                      }
                      className={NUMBER_INPUT}
                      disabled={isLoading}
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <label htmlFor={maxDepthId} className={FIELD_LABEL}>
                      Max depth
                    </label>
                    <input
                      id={maxDepthId}
                      type="number"
                      min={1}
                      max={MAX_CRAWL_DEPTH}
                      value={maxDepth}
                      onChange={(e) =>
                        setMaxDepth(
                          clampNumber(e.target.value, maxDepth, 1, MAX_CRAWL_DEPTH)
                        )
                      }
                      className={NUMBER_INPUT}
                      disabled={isLoading}
                    />
                  </div>
                </div>
                <p className="text-[11px] leading-4 text-muted-foreground">
                  {singleFile
                    ? "Single file: one index.html, one model call. Sites too large for one answer switch to a file per page automatically."
                    : "Each page is a separate model call. Free models take roughly 1-2 minutes per page."}
                </p>
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={generateDatabase}
                      onChange={(e) => setGenerateDatabase(e.target.checked)}
                      disabled={isLoading}
                      className="h-3.5 w-3.5 rounded border-input accent-brand"
                    />
                    Database schema
                  </label>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={generateAuth}
                      onChange={(e) => setGenerateAuth(e.target.checked)}
                      disabled={isLoading}
                      className="h-3.5 w-3.5 rounded border-input accent-brand"
                    />
                    Auth pages
                  </label>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={singleFile}
                      onChange={(e) => setSingleFile(e.target.checked)}
                      disabled={isLoading}
                      className="h-3.5 w-3.5 rounded border-input accent-brand"
                    />
                    Single file
                  </label>
                </div>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3 border-t border-border bg-muted/30 px-5 py-3.5">
          <div className="min-w-0 text-xs text-muted-foreground">
            {isLoading && elapsed && (
              <span className="tabular-nums">Elapsed {elapsed}</span>
            )}
          </div>
          <div className="w-full sm:w-56">
            {isLoading ? (
              <button
                type="button"
                onClick={cancelCrawl}
                className="w-full rounded-lg border border-border bg-card px-4 py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                data-testid="clone-cancel"
              >
                Cancel
              </button>
            ) : (
              <button
                type="button"
                onClick={handleStart}
                className="group flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-brand-foreground shadow-card transition-all hover:bg-brand-muted hover:shadow-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card"
                data-testid="clone-start"
              >
                {phase === "failed" || phase === "cancelled"
                  ? "Try again"
                  : "Clone website"}
                <LuArrowRight
                  className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                  aria-hidden="true"
                />
              </button>
            )}
          </div>
        </div>
      </div>

      {state.error && (
        <div
          role="alert"
          className="rounded-xl border border-danger-border bg-danger-subtle px-4 py-3"
        >
          <div className="flex items-start gap-2.5">
            <LuAlertTriangle
              className="mt-0.5 h-4 w-4 shrink-0 text-danger"
              aria-hidden="true"
            />
            <div className="min-w-0">
              <p className="text-sm font-medium text-danger">Clone failed</p>
              <p className="mt-1 break-words text-xs leading-5 text-danger/90">
                {state.error}
              </p>
            </div>
          </div>
        </div>
      )}

      {phase === "cancelled" && !state.error && (
        <div
          role="status"
          className="flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-3 text-xs text-muted-foreground"
        >
          <LuRotateCcw className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          Run cancelled
          {state.completedPages.length > 0 &&
            ` after ${state.completedPages.length} page${state.completedPages.length === 1 ? "" : "s"}`}
        </div>
      )}

      {phase !== "idle" && (
        <div className="overflow-hidden rounded-xl border border-border bg-card shadow-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
            <h4 className="flex items-center gap-2 text-xs font-medium text-foreground">
              Progress
              {isLoading && (
                <span className="working-indicator-bg h-1 w-16 rounded-full" />
              )}
            </h4>
            <div className="flex items-center gap-2.5 text-[11px] text-muted-foreground">
              {totalPages > 0 && (
                <span className="tabular-nums">
                  {donePages}/{totalPages}
                </span>
              )}
              {elapsed && <span className="tabular-nums">{elapsed}</span>}
            </div>
          </div>

          {totalPages > 0 && (
            <div
              className="h-1 w-full bg-border"
              role="progressbar"
              aria-label="Pages generated"
              aria-valuemin={0}
              aria-valuemax={totalPages}
              aria-valuenow={donePages}
            >
              <div
                className="h-full bg-gradient-to-r from-brand-muted to-brand transition-all duration-500 ease-out"
                style={{ width: `${percent}%` }}
              />
            </div>
          )}

          <div
            className="max-h-56 space-y-2 overflow-y-auto px-4 py-3"
            role="status"
            aria-live="polite"
          >
            {isLoading && statusLine && (
              <div className="flex items-center gap-2 text-xs text-foreground">
                <LuLoader2
                  className="h-3 w-3 shrink-0 animate-spin text-brand"
                  aria-hidden="true"
                />
                <span className="min-w-0 break-words">{statusLine}</span>
                {state.progress && state.progress.total > 0 && (
                  <span className="shrink-0 tabular-nums text-muted-foreground">
                    ({state.progress.current}/{state.progress.total})
                  </span>
                )}
              </div>
            )}

            {state.crawlComplete && (
              <div
                className={`flex items-center gap-2 text-xs ${
                  state.crawlComplete.pagesFound > 0
                    ? "text-muted-foreground"
                    : "text-warning"
                }`}
              >
                {state.crawlComplete.pagesFound > 0 ? (
                  <LuCheck
                    className="h-3 w-3 shrink-0 text-success"
                    aria-hidden="true"
                  />
                ) : (
                  <LuAlertTriangle className="h-3 w-3 shrink-0" aria-hidden="true" />
                )}
                <span>
                  Crawl complete — {state.crawlComplete.pagesFound} page
                  {state.crawlComplete.pagesFound === 1 ? "" : "s"} found
                </span>
              </div>
            )}

            {(state.completedPages.length > 0 ||
              state.failedPages.length > 0) && (
              <ul className="space-y-1.5 border-t border-border pt-2">
                {state.completedPages.map((path, index) => (
                  <li
                    key={`done-${index}-${path}`}
                    className="flex items-center gap-2 text-xs text-muted-foreground"
                  >
                    <LuCheck
                      className="h-3 w-3 shrink-0 text-success"
                      aria-hidden="true"
                    />
                    <span className="min-w-0 truncate font-mono text-[11px]">
                      {path}
                    </span>
                  </li>
                ))}

                {state.failedPages.map((path, index) => (
                  <li
                    key={`failed-${index}-${path}`}
                    className="flex items-center gap-2 text-xs text-danger"
                  >
                    <LuX className="h-3 w-3 shrink-0" aria-hidden="true" />
                    <span className="min-w-0 truncate font-mono text-[11px]">
                      {path}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default UrlToCodePane;
