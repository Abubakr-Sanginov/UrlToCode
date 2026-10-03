import { create } from "zustand";
import { persist } from "zustand/middleware";
import { CloneStack, NEXTJS_STACK, Stack } from "../lib/stacks";

/**
 * State of the URL clone flow that must outlive the clone pane: the stack the
 * user picked (kept across Settings and reloads) and what the last clone was
 * made from - the live URL and a full-page screenshot of every crawled page,
 * keyed by page path. The preview shows those next to the clone.
 */
interface CloneStore {
  cloneStack: CloneStack;
  setCloneStack: (stack: CloneStack) => void;
  baseUrl: string | null;
  /**
   * Server-side id of the run that produced the current clone. The backend
   * keeps the crawl and every generated page under it, which is what lets a
   * single page be regenerated or scored later without crawling again.
   */
  runId: string | null;
  /**
   * Files the run produced beyond its pages: the generated server
   * ("server/app.py") and the captured API mock layer ("mock/api.json"),
   * keyed by the path they take in the project. Kept out of the version list
   * on purpose: these are project files, not pages a visitor sees, and
   * listing them as versions would put `app.py` in the page switcher.
   */
  generatedFiles: Record<string, string>;
  /**
   * The run whose dev server is running on this backend, or null. The
   * editor reads it to decide whether an edit can be pushed to disk for a
   * hot reload, and a stale value here costs one unnecessary request.
   */
  devServerRunId: string | null;
  setDevServerRunId: (runId: string | null) => void;
  screenshots: Record<string, string>;
  sourceUrls: Record<string, string>;
  setCloneSource: (source: {
    baseUrl?: string | null;
    runId?: string | null;
    screenshots?: Record<string, string>;
    sourceUrls?: Record<string, string>;
    generatedFiles?: Record<string, string>;
  }) => void;
  resetCloneSource: () => void;
}

const VALID_STACKS: readonly string[] = [NEXTJS_STACK, ...Object.values(Stack)];

export const useCloneStore = create<CloneStore>()(
  persist(
    (set) => ({
      // A framework project is the point of the tool; HTML is opt-in.
      cloneStack: NEXTJS_STACK,
      setCloneStack: (cloneStack) => set({ cloneStack }),
      baseUrl: null,
      runId: null,
      screenshots: {},
      sourceUrls: {},
      generatedFiles: {},
      devServerRunId: null,
      setDevServerRunId: (devServerRunId) => set({ devServerRunId }),
      setCloneSource: (source) =>
        set({
          baseUrl: source.baseUrl ?? null,
          runId: source.runId ?? null,
          screenshots: source.screenshots ?? {},
          sourceUrls: source.sourceUrls ?? {},
          generatedFiles: source.generatedFiles ?? {},
        }),
      resetCloneSource: () =>
        set({
          baseUrl: null,
          runId: null,
          screenshots: {},
          sourceUrls: {},
          generatedFiles: {},
        }),
    }),
    {
      name: "clone-settings",
      // Only the choice is remembered; screenshots belong to one run.
      partialize: (state) => ({ cloneStack: state.cloneStack }),
      merge: (persisted, current) => {
        const stored = (persisted as { cloneStack?: string } | undefined)?.cloneStack;
        return {
          ...current,
          cloneStack:
            stored && VALID_STACKS.includes(stored)
              ? (stored as CloneStack)
              : current.cloneStack,
        };
      },
    }
  )
);

/** Page path a preview commit shows; the portal stands in for the home page. */
export function pagePathForLabel(label: string | undefined): string | null {
  if (!label) return null;
  if (label === "Portal") return "/";
  return label.startsWith("/") ? label : null;
}
