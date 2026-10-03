import { HTTP_BACKEND_URL } from "../config";
import { Settings } from "../types";

/**
 * Regenerating a single page of a finished clone.
 *
 * The clone run lives on the backend: it still holds the crawl every page was
 * generated from. That is what makes this possible without crawling the site
 * again, and without asking the model to redo the pages around this one.
 */

export interface CloneRunPage {
  path: string;
  status: string;
  code: string;
  error: string;
  attempts: number;
  /** Visual agreement with the original, 0..1. Absent until scored. */
  fidelity: number | null;
  updated_at: number;
}

export interface CloneRunDetail {
  runId: string;
  baseUrl: string;
  stack: string;
  phase: string;
  pages: Record<string, CloneRunPage>;
  code: Record<string, string>;
}

export interface CloneRunSummary {
  runId: string;
  baseUrl: string;
  stack: string;
  phase: string;
  error: string;
  createdAt: number;
  updatedAt: number;
  pageCount: number;
  pagesComplete: number;
  pagesFailed: number;
  meanFidelity: number | null;
}

export class CloneRunError extends Error {}

async function readError(response: Response): Promise<string> {
  try {
    const body = await response.json();
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  } catch {
    // A non-JSON error body is common enough (proxy pages) to fall through.
  }
  return `Request failed (${response.status})`;
}

/** Whether a model can see images, and how that was established. */
export interface VisionAnswer {
  model: string;
  /** "yes", "no", or "unknown" - never conflated, because they differ. */
  capability: "yes" | "no" | "unknown";
  /** "name" for a known model, "probe" for one we actually asked. */
  source: "name" | "probe";
  detail: string;
}

export async function detectCloneVision(
  params: Settings,
  refresh = false
): Promise<VisionAnswer> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/clone-vision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...providerFields(params), refresh }),
  });
  if (!response.ok) throw new CloneRunError(await readError(response));
  return (await response.json()) as VisionAnswer;
}

/** What a run of this size is likely to cost, and where the calls go. */
export interface CloneCostEstimate {
  pages: {
    pages: number;
    model: string;
    perPage: number;
    perPageText: string;
    total: number;
    totalText: string;
  };
  routing: {
    pageModel: string;
    auxiliaryModel: string;
    reasons: Record<string, string>;
    vision: "yes" | "no" | "unknown";
    seesImages: boolean;
  };
  vision: VisionAnswer;
}

export async function estimateCloneCost(
  params: Settings & { url: string; maxPages: number }
): Promise<CloneCostEstimate> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/clone-cost/estimate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url: params.url,
      maxPages: params.maxPages,
      ...providerFields(params),
    }),
  });
  if (!response.ok) throw new CloneRunError(await readError(response));
  return (await response.json()) as CloneCostEstimate;
}

/** Where a run is, and how much of it is still to do. */
export interface CloneRunStatus {
  runId: string;
  /** False when this server no longer has the run at all. */
  known: boolean;
  running: boolean;
  phase?: string;
  error?: string;
  pagesTotal?: number;
  pagesDone?: number;
  pagesFailed?: number;
  pagesPending?: number;
  stack?: string;
  baseUrl?: string;
}

export async function fetchCloneRunStatus(runId: string): Promise<CloneRunStatus> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/clone-runs/${encodeURIComponent(runId)}/status`
  );
  if (!response.ok) throw new CloneRunError(await readError(response));
  return (await response.json()) as CloneRunStatus;
}

/**
 * Carry a stopped run on from whatever it already finished.
 *
 * Only the pages with no code are generated, so a run that got seven of
 * twelve pages through costs the remaining five rather than all twelve
 * again. The key is sent here and nowhere else - the run itself never
 * stores one.
 */
export async function resumeCloneRun(runId: string, settings: Settings): Promise<CloneRunStatus> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/clone-runs/${encodeURIComponent(runId)}/resume`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(providerFields(settings)),
    }
  );
  if (!response.ok) throw new CloneRunError(await readError(response));
  return (await response.json()) as CloneRunStatus;
}

/** Stop a run that is still going, keeping every page it already wrote. */
export async function stopCloneRun(runId: string): Promise<boolean> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/clone-runs/${encodeURIComponent(runId)}/stop`,
    { method: "POST" }
  );
  if (!response.ok) throw new CloneRunError(await readError(response));
  const body = (await response.json()) as { stopped?: boolean };
  return body.stopped === true;
}

/**
 * The provider credentials of a settings object, in the shape the API takes.
 *
 * Only the keys the settings actually hold are sent: the model ids are left to
 * the backend's own defaults, exactly as the clone websocket does, so a page
 * regenerated later is not forced onto a different model than the run used.
 */
function providerFields(settings: Settings): Record<string, unknown> {
  return {
    openaiApiKey: settings.openAiApiKey,
    openAiBaseURL: settings.openAiBaseURL,
    anthropicApiKey: settings.anthropicApiKey,
    geminiApiKey: settings.geminiApiKey,
    openRouterApiKey: settings.openRouterApiKey,
    openRouterModel: settings.openRouterModel,
    reasoningEffort: settings.reasoningEffort,
    customProviderBaseUrl: settings.customProviderBaseUrl,
    customProviderApiKey: settings.customProviderApiKey,
    customProviderModel: settings.customProviderModel,
  };
}

export interface RegenerateOptions {
  runId: string;
  path: string;
  settings: Settings;
  /**
   * What to change about the page as it stands. Without it the page is simply
   * generated again from the original crawl; with it the model is shown the
   * current output and asked to correct that instead.
   */
  instruction?: string;
  stack?: string;
  generateDatabase?: boolean;
}

/**
 * Generate one page of a stored run again.
 *
 * Throws `CloneRunError` with the server's own reason, so the caller can show
 * "the model did not return a usable page" instead of a generic failure.
 */
export async function regeneratePage(options: RegenerateOptions): Promise<string> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/clone-runs/${encodeURIComponent(options.runId)}/regenerate-page`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path: options.path,
        instruction: options.instruction ?? "",
        stack: options.stack,
        generateDatabase: options.generateDatabase,
        ...providerFields(options.settings),
      }),
    }
  );
  if (!response.ok) throw new CloneRunError(await readError(response));
  const body = (await response.json()) as { code?: string };
  if (typeof body.code !== "string" || !body.code) {
    throw new CloneRunError("The model returned no code for this page.");
  }
  return body.code;
}

/** An area of a page that renders differently from the original. */
export interface DiffRegion {
  x: number;
  y: number;
  width: number;
  height: number;
  /** How wrong this area is, 0..1. */
  severity: number;
}

export interface ViewportFidelity {
  /** "desktop", "mobile" or "tablet". */
  name: string;
  width: number;
  score: number;
  heightRatio: number;
  originalWidth: number;
  originalHeight: number;
  renderedWidth: number;
  renderedHeight: number;
  regions: DiffRegion[];
  error: string;
  renderFile: string;
  originalFile: string;
}

export interface PageFidelity {
  path: string;
  /** How close the page is to the original, 0..1 - the worst of its widths. */
  score: number;
  /** How much taller or shorter the clone is than the original. */
  heightRatio: number;
  /** The original screenshot's real pixel size; the regions are in it. */
  originalWidth: number;
  originalHeight: number;
  /** The clone's render, at the same width as the original. */
  renderedWidth: number;
  renderedHeight: number;
  regions: DiffRegion[];
  /** Set when the page could not be checked at all. */
  error: string;
  /** The backend's copy of the rendered page, for the visual diff. */
  renderFile: string;
  /** The original screenshot it was compared against. */
  originalFile: string;
  /** One entry per width checked; only the desktop one for a plain crawl. */
  viewports: ViewportFidelity[];
}

export interface RunFidelity {
  runId: string;
  pages: PageFidelity[];
  /** Pages that could not be checked (no original, no renderer, no code). */
  skipped: string[];
  meanScore: number;
  threshold: number;
  needsRepair: string[];
}

export interface CheckOptions {
  runId: string;
  /** Omitted means every page of the run. */
  paths?: string[];
  threshold?: number;
}

/**
 * Render a run's pages and score them against the originals captured while
 * crawling. The backend records the score on the run, so it is not lost when
 * the tab is closed.
 */
export async function checkCloneFidelity(
  options: CheckOptions
): Promise<RunFidelity> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/clone-runs/${encodeURIComponent(options.runId)}/visual-check`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: options.paths ?? null, threshold: options.threshold ?? null }),
    }
  );
  if (!response.ok) throw new CloneRunError(await readError(response));
  return normaliseFidelity((await response.json()) as Partial<RunFidelity>);
}

export interface RepairOptions extends CheckOptions {
  settings: Settings;
  /** Extra generations for one page before moving to the next. */
  maxAttempts?: number;
  /** How many pages one call may repair, so the cost stays predictable. */
  maxPages?: number;
}

export interface RepairOutcome {
  repaired: { path: string; attempt: number; score: number; passed: boolean; error: string }[];
  skipped: string[];
  /** Why the run stopped early, or "" when it finished. */
  stopped: string;
  /** The run's code after the repairs, so the preview can update. */
  code: Record<string, string>;
}

/** Regenerate the pages that scored below the threshold, then re-check them. */
export async function repairCloneFidelity(
  options: RepairOptions
): Promise<RepairOutcome> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/clone-runs/${encodeURIComponent(options.runId)}/repair`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paths: options.paths ?? null,
        threshold: options.threshold ?? null,
        maxAttempts: options.maxAttempts ?? 2,
        maxPages: options.maxPages ?? 3,
        ...providerFields(options.settings),
      }),
    }
  );
  if (!response.ok) throw new CloneRunError(await readError(response));
  const body = (await response.json()) as Partial<RepairOutcome>;
  return {
    repaired: Array.isArray(body.repaired) ? body.repaired : [],
    skipped: Array.isArray(body.skipped) ? body.skipped : [],
    stopped: typeof body.stopped === "string" ? body.stopped : "",
    code: (body.code ?? {}) as Record<string, string>,
  };
}

/** What a fidelity score means, in words the UI can show. */
export type FidelityVerdict = "close" | "close-enough" | "off" | "unknown";

export function fidelityVerdict(score: number | null, threshold = 0.8): FidelityVerdict {
  if (score === null || Number.isNaN(score)) return "unknown";
  if (score >= 0.95) return "close";
  if (score >= threshold) return "close-enough";
  return "off";
}

export function fidelityLabel(verdict: FidelityVerdict): string {
  switch (verdict) {
    case "close":
      return "Near-identical";
    case "close-enough":
      return "Close enough";
    case "off":
      return "Differs from the original";
    default:
      return "Not checked yet";
  }
}

function normaliseViewport(entry: Partial<ViewportFidelity>): ViewportFidelity {
  return {
    name: entry.name ?? "desktop",
    width: typeof entry.width === "number" ? entry.width : 0,
    score: typeof entry.score === "number" ? entry.score : 0,
    heightRatio: typeof entry.heightRatio === "number" ? entry.heightRatio : 1,
    originalWidth: typeof entry.originalWidth === "number" ? entry.originalWidth : 0,
    originalHeight: typeof entry.originalHeight === "number" ? entry.originalHeight : 0,
    renderedWidth: typeof entry.renderedWidth === "number" ? entry.renderedWidth : 0,
    renderedHeight: typeof entry.renderedHeight === "number" ? entry.renderedHeight : 0,
    regions: Array.isArray(entry.regions) ? entry.regions : [],
    error: entry.error ?? "",
    renderFile: entry.renderFile ?? "",
    originalFile: entry.originalFile ?? "",
  };
}

function normalisePage(entry: Partial<PageFidelity>): PageFidelity {
  return {
    path: entry.path ?? "",
    score: typeof entry.score === "number" ? entry.score : 0,
    heightRatio: typeof entry.heightRatio === "number" ? entry.heightRatio : 1,
    originalWidth: typeof entry.originalWidth === "number" ? entry.originalWidth : 0,
    originalHeight: typeof entry.originalHeight === "number" ? entry.originalHeight : 0,
    renderedWidth: typeof entry.renderedWidth === "number" ? entry.renderedWidth : 0,
    renderedHeight: typeof entry.renderedHeight === "number" ? entry.renderedHeight : 0,
    regions: Array.isArray(entry.regions) ? entry.regions : [],
    error: entry.error ?? "",
    renderFile: entry.renderFile ?? "",
    originalFile: entry.originalFile ?? "",
    // A run checked before multi-width support has no viewports; its single
    // desktop result is described by the page's own fields, so the list is
    // rebuilt from them - but only when there is a result at all. A page that
    // errored was never checked, and inventing a 0% for it would report a
    // failure that did not happen.
    viewports: Array.isArray(entry.viewports)
      ? entry.viewports.map(normaliseViewport)
      : entry.renderFile
        ? [normaliseViewport({ ...entry, name: "desktop" })]
        : [],
  };
}

function normaliseFidelity(body: Partial<RunFidelity>): RunFidelity {
  return {
    runId: body.runId ?? "",
    pages: Array.isArray(body.pages) ? body.pages.map(normalisePage) : [],
    skipped: Array.isArray(body.skipped) ? body.skipped : [],
    meanScore: typeof body.meanScore === "number" ? body.meanScore : 0,
    threshold: typeof body.threshold === "number" ? body.threshold : 0.8,
    needsRepair: Array.isArray(body.needsRepair) ? body.needsRepair : [],
  };
}
