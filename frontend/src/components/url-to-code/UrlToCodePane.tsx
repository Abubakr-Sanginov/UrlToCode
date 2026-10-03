import { useCallback, useEffect, useId, useState } from "react";
import {
  LuLoader2,
  LuCheck,
  LuX,
  LuChevronDown,
  LuChevronUp,
  LuAlertTriangle,
  LuRotateCcw,
  LuArrowRight,
  LuEye,
  LuEyeOff,
  LuHelpCircle,
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
import {
  CloneStack,
  NEXTJS_STACK,
  Stack,
  cloneStackLabel,
  isFrameworkStack,
} from "../../lib/stacks";
import { useCloneStore } from "../../store/clone-store";
import { CloneCostEstimate, estimateCloneCost } from "../../lib/cloneRuns";
import { useAccountUi } from "../../store/account-ui-store";
import { useAccount } from "../../hooks/useAccount";
import { Settings } from "../../types";
import {
  CrawlRunState,
  MAX_CRAWL_DEPTH,
  MAX_RETRIES,
  MAX_CRAWL_PAGES,
  PageSelection,
  StartCrawlParams,
} from "../../hooks/useUrlToCode";
import { useElapsedTime } from "../../hooks/useElapsedTime";
import {
  pickAll,
  pickNone,
  selectionSummary,
  togglePicked,
} from "../../lib/pageSelection";

interface Props {
  startCrawl: (params: StartCrawlParams) => void;
  cancelCrawl: () => void;
  /** Answer the page-selection question, or decline to build anything. */
  choosePages: (paths: string[]) => void;
  state: CrawlRunState;
  settings: Settings;
}

// Next.js first: it is the most common target for a cloned site.
const CLONE_STACKS: CloneStack[] = [NEXTJS_STACK, ...Object.values(Stack)];

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

/**
 * Which of the crawled pages to build.
 *
 * One model call per page, so a crawler that followed every link can come
 * back with forty pages the user never wanted rebuilt. The ticking starts
 * empty rather than everything-ticked: a list where everything is already
 * on is a list nobody reads, and the pages most likely to be unwanted are
 * exactly the ones that scroll off the bottom.
 */
function PageChooser({
  selection,
  picked,
  onToggle,
  onAll,
  onNone,
  onConfirm,
}: {
  selection: PageSelection;
  picked: Set<string>;
  onToggle: (path: string) => void;
  onAll: () => void;
  onNone: () => void;
  onConfirm: () => void;
}) {
  const total = selection.pages.length;
  return (
    <div
      className="rounded-2xl border border-border bg-card p-4 shadow-raised"
      data-testid="page-chooser"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-medium text-foreground">
            {total} pages found
          </h2>
          <p className="text-xs text-muted-foreground">
            Tick the pages to build. Unticked pages cost nothing.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onAll}
            className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            All
          </button>
          <button
            type="button"
            onClick={onNone}
            className="rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            None
          </button>
        </div>
      </div>

      <ul className="mt-3 max-h-64 space-y-1 overflow-y-auto">
        {selection.pages.map((page) => (
          <li key={page.path}>
            <label className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent">
              <input
                type="checkbox"
                checked={picked.has(page.path)}
                onChange={() => onToggle(page.path)}
                className="h-3.5 w-3.5 shrink-0 rounded border-input accent-brand"
              />
              <span className="min-w-0 flex-1 truncate text-foreground">
                {page.title || page.path}
              </span>
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {page.path}
              </span>
            </label>
          </li>
        ))}
      </ul>

      <div className="mt-3 flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground" data-testid="page-chooser-count">
          {picked.size} of {total} selected
        </p>
        <button
          type="button"
          onClick={onConfirm}
          className="rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-foreground transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-card"
          data-testid="page-chooser-confirm"
        >
          {selectionSummary(picked)}
        </button>
      </div>
    </div>
  );
}

const FIELD_LABEL =
  "block text-xs font-medium text-muted-foreground";

const NUMBER_INPUT =
  "h-8 w-16 rounded-md border border-input bg-background px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-card disabled:opacity-50";

function UrlToCodePane({
  startCrawl,
  cancelCrawl,
  choosePages,
  state,
  settings,
}: Props) {
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(10);
  const [maxDepth, setMaxDepth] = useState(2);
  const [maxRetries, setMaxRetries] = useState(1);
  const [generateDatabase, setGenerateDatabase] = useState(false);
  const [generateAuth, setGenerateAuth] = useState(false);
  // Lives in a store, not here: opening Settings unmounts this pane.
  const { cloneStack, setCloneStack } = useCloneStore();
  const isFramework = isFrameworkStack(cloneStack);
  // One file per page by default; a single file is opt-in.
  const [singleFile, setSingleFile] = useState(false);
  // Capture every page at phone, tablet and desktop. Off by default because
  // it roughly triples the crawl's capture time, and most sites are cloned
  // for desktop use.
  const [responsive, setResponsive] = useState(false);
  // The only option that makes the crawl act rather than watch. Off by
  // default, and the label says so.
  const [captureInteractions, setCaptureInteractions] = useState(false);
  const [confirmPages, setConfirmPages] = useState(false);
  // A hard ceiling for one run. Zero means no ceiling, which is the only
  // safe default: silently spending nothing would break every clone.
  const [maxCost, setMaxCost] = useState(0);
  // What a run of this size is likely to cost, asked for when the URL and
  // page count settle rather than on every keystroke.
  const [estimate, setEstimate] = useState<CloneCostEstimate | null>(null);
  const [isEstimating, setIsEstimating] = useState(false);
  // Whether anyone is signed in, and what is left of the day's allowance.
  // The button reads this so the state of the account is visible before a
  // run is started rather than in the refusal that follows it.
  const { signedIn, usage: accountUsage } = useAccount();
  // The dialog lives at the app level so the icon strip can open it too.
  const openSignIn = useAccountUi((state) => state.openSignIn);
  const onSignIn = useCallback(() => openSignIn(), [openSignIn]);
  // Which of the crawled pages are ticked. Empty means "none", which is a
  // decision the user can make on purpose - so it is not the same as unset.
  const [pickedPaths, setPickedPaths] = useState<Set<string>>(new Set());
  const [showAdvanced, setShowAdvanced] = useState(false);

  const ids = useId();
  const urlId = `${ids}-url`;
  const stackId = `${ids}-stack`;
  const maxPagesId = `${ids}-max-pages`;
  const maxDepthId = `${ids}-max-depth`;
  const maxRetriesId = `${ids}-max-retries`;
  const maxCostId = `${ids}-max-cost`;
  const advancedId = `${ids}-advanced`;

  const { isLoading, phase } = state;
  const elapsed = useElapsedTime(state.startedAt, isLoading);
  const providerConfigured = hasAnyProvider(settings);

  const totalPages =
    state.currentPage?.totalPages ?? state.crawlComplete?.pagesFound ?? 0;
  const donePages = state.completedPages.length + state.failedPages.length;
  const percent =
    totalPages > 0 ? Math.round((donePages / totalPages) * 100) : 0;

  // Asked once the URL looks like a URL and the page count has settled.
  // The point is to see the number before starting, not on every keystroke.
  const settledUrl = url.trim();
  const canEstimate =
    /^https?:\/\/\S+$/i.test(settledUrl) && providerConfigured && !isLoading;
  useEffect(() => {
    if (!canEstimate) {
      setEstimate(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setIsEstimating(true);
      estimateCloneCost({ ...settings, url: settledUrl, maxPages })
        .then((result) => {
          if (!cancelled) setEstimate(result);
        })
        .catch(() => {
          // No estimate is a quiet absence, not an error: the run itself
          // does not depend on it.
          if (!cancelled) setEstimate(null);
        })
        .finally(() => {
          if (!cancelled) setIsEstimating(false);
        });
    }, 500);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [canEstimate, settledUrl, maxPages, settings]);

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
      stack: cloneStack,
      maxPages,
      maxDepth,
      maxRetries,
      generateDatabase,
      generateAuth,
      // A framework project is one component per route.
      singleFile: singleFile && !isFramework,
      responsive,
      captureInteractions,
      confirmPages,
      maxCost,
      settings,
    });
  }

  const statusLine = state.currentPage
    ? `Generating ${state.currentPage.path} (${state.currentPage.pageIndex + 1}/${state.currentPage.totalPages})`
    : state.status;

  return (
    <div className="flex w-full flex-col gap-3">
      {state.pageSelection && (
        <PageChooser
          selection={state.pageSelection}
          picked={pickedPaths}
          onToggle={(path) => setPickedPaths((previous) => togglePicked(previous, path))}
          onAll={() => setPickedPaths(pickAll(state.pageSelection!.pages))}
          onNone={() => setPickedPaths(pickNone())}
          onConfirm={() => choosePages([...pickedPaths])}
        />
      )}
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
              className="mt-1.5 h-11 w-full font-mono text-sm"
              disabled={isLoading}
              data-testid="clone-url-input"
            />
          </div>

          <div className="flex items-center gap-3">
            <label htmlFor={stackId} className={FIELD_LABEL}>
              Stack
            </label>
            <Select
              value={cloneStack}
              onValueChange={(value) => setCloneStack(value as CloneStack)}
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
                  {CLONE_STACKS.map((s) => (
                    <SelectItem key={s} value={s}>
                      <span className="text-sm">{cloneStackLabel(s)}</span>
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
                  <div className="flex items-center gap-2">
                    <label
                      htmlFor={maxRetriesId}
                      className={FIELD_LABEL}
                      title="Extra attempts for the whole run, shared by every page"
                    >
                      Max retries
                    </label>
                    <input
                      id={maxRetriesId}
                      type="number"
                      min={0}
                      max={MAX_RETRIES}
                      value={maxRetries}
                      onChange={(e) =>
                        setMaxRetries(clampNumber(e.target.value, maxRetries, 0, MAX_RETRIES))
                      }
                      className={NUMBER_INPUT}
                      disabled={isLoading}
                    />
                  </div>
                </div>
                <p className="text-[11px] leading-4 text-muted-foreground">
                  {isFramework
                    ? "Each page becomes a TypeScript page component in a runnable project (npm install && npm run dev)."
                    : singleFile
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
                      checked={singleFile && !isFramework}
                      onChange={(e) => setSingleFile(e.target.checked)}
                      disabled={isLoading || isFramework}
                      className="h-3.5 w-3.5 rounded border-input accent-brand"
                    />
                    Single file
                  </label>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={responsive}
                      onChange={(e) => setResponsive(e.target.checked)}
                      disabled={isLoading}
                      className="h-3.5 w-3.5 rounded border-input accent-brand"
                      data-testid="clone-responsive"
                    />
                    Responsive
                  </label>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={captureInteractions}
                      onChange={(e) => setCaptureInteractions(e.target.checked)}
                      disabled={isLoading}
                      className="h-3.5 w-3.5 rounded border-input accent-brand"
                      data-testid="clone-interactions"
                    />
                    Capture open menus and dialogs
                  </label>
                  <label className="flex items-center gap-2 text-xs text-muted-foreground">
                    <input
                      type="checkbox"
                      checked={confirmPages}
                      onChange={(e) => setConfirmPages(e.target.checked)}
                      disabled={isLoading}
                      className="h-3.5 w-3.5 rounded border-input accent-brand"
                      data-testid="clone-confirm-pages"
                    />
                    Choose pages
                  </label>
                </div>
                <p className="text-[11px] leading-4 text-muted-foreground">
                  {confirmPages
                    ? "After the crawl, every page it found is listed and only the ones you tick are generated. The rest cost nothing."
                    : captureInteractions
                      ? "The crawl will click menus, accordions and tabs to see what they open. It never submits a form, types into a field, or follows a link off the site."
                      : responsive
                        ? "Every page is captured at 1440, 768 and 375 pixels, and the clone is built and checked at all three. Slower crawl, far better mobile."
                        : "Only the desktop layout is captured. Turn Responsive on for a clone that works on a phone."}
                </p>

                {/* What it is likely to cost, and the stop that keeps it
                    bounded. The estimate is the only number the user can act
                    on; after the run starts the money is already spent. */}
                <div>
                  <div className="flex items-end justify-between gap-3">
                    <div>
                      <label htmlFor={maxCostId} className={FIELD_LABEL}>
                        Spending limit
                      </label>
                      <p className="text-[11px] leading-4 text-muted-foreground">
                        The run stops before it passes this. Everything already
                        generated is kept.
                      </p>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs text-muted-foreground">$</span>
                      <input
                        id={maxCostId}
                        type="number"
                        min={0}
                        step={0.25}
                        value={maxCost || ""}
                        placeholder="no limit"
                        onChange={(e) =>
                          setMaxCost(Math.max(0, Number(e.target.value) || 0))
                        }
                        disabled={isLoading}
                        className={NUMBER_INPUT}
                        data-testid="clone-max-cost"
                      />
                    </div>
                  </div>
                  {estimate && (
                    <p
                      className="mt-2 text-[11px] leading-4 text-muted-foreground"
                      data-testid="clone-cost-estimate"
                    >
                      {isEstimating
                        ? "Estimating..."
                        : `About ${estimate.pages.totalText} for ${maxPages} page${
                            maxPages === 1 ? "" : "s"
                          } on ${estimate.pages.model} (${estimate.pages.perPageText} each).`}
                    </p>
                  )}
                  {/* Whether the chosen model can look at pictures decides
                      whether a visual repair is made from the two
                      screenshots or from a description of them. Saying it here
                      is the difference between a real fix and a confident
                      guess the user cannot tell apart from one. */}
                  {estimate && (
                    <p
                      className="mt-1.5 flex items-start gap-1.5 text-[11px] leading-4"
                      data-testid="clone-vision"
                    >
                      {estimate.vision.capability === "yes" ? (
                        <>
                          <LuEye
                            className="mt-px h-3.5 w-3.5 shrink-0 text-emerald-600"
                            aria-hidden="true"
                          />
                          <span className="text-muted-foreground">
                            {estimate.pages.model} sees images — it does every part of the
                            job, visual repairs included.
                          </span>
                        </>
                      ) : estimate.vision.capability === "no" ? (
                        <>
                          <LuEyeOff
                            className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600"
                            aria-hidden="true"
                          />
                          <span className="text-muted-foreground">
                            {estimate.pages.model} cannot see images — visual repairs
                            are made from a description of the differences.
                          </span>
                        </>
                      ) : (
                        <>
                          <LuHelpCircle
                            className="mt-px h-3.5 w-3.5 shrink-0 text-muted-foreground"
                            aria-hidden="true"
                          />
                          <span className="text-muted-foreground">
                            {estimate.vision.detail ||
                              `Could not confirm whether ${estimate.pages.model} sees images.`}
                          </span>
                        </>
                      )}
                    </p>
                  )}
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
            {!signedIn ? (
              // Asking to sign in here rather than letting the run be
              // refused: the button does the same thing either way, and a
              // refusal after the click is a worse first impression.
              <button
                type="button"
                onClick={onSignIn}
                className="w-full rounded-lg bg-brand px-4 py-2.5 text-sm font-semibold text-brand-foreground shadow-card transition-all hover:bg-brand-muted"
                data-testid="clone-signin"
              >
                Sign in to clone
              </button>
            ) : isLoading ? (
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
            {signedIn && accountUsage && accountUsage.remaining === 0 && (
              <p className="mt-2 text-center text-xs text-muted-foreground">
                Today&apos;s runs are used up. They come back at midnight.
              </p>
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

      {/* The sign-in dialog itself is rendered once, by the app: this pane
          and the icon strip both open the same one. */}    </div>
  );
}

export default UrlToCodePane;
