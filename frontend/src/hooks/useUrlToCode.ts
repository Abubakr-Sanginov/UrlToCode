import { useCallback, useEffect, useRef, useState } from "react";
import { WS_BACKEND_URL } from "../config";
import { USER_CLOSE_WEB_SOCKET_CODE } from "../constants";
import { Settings } from "../types";
import toast from "react-hot-toast";

// Kept in sync with the backend clamps in routes/url_to_code.py so the UI
// cannot offer values the server would silently discard.
export const MAX_CRAWL_PAGES = 30;
export const MAX_CRAWL_DEPTH = 6;

export interface PageInfo {
  path: string;
  title: string;
  depth: number;
}

export interface CrawlComplete {
  pagesFound: number;
  pages: PageInfo[];
}

export interface PageGenProgress {
  pageIndex: number;
  totalPages: number;
  path: string;
  codeLength?: number;
  error?: boolean;
}

export type CrawlPhase =
  | "idle"
  | "crawling"
  | "generating"
  | "done"
  | "failed"
  | "cancelled";

export interface CrawlRunState {
  phase: CrawlPhase;
  /** Latest coarse status line, e.g. "Generating page 3/12". */
  status: string;
  /** Crawl-phase counter. Page generation uses `currentPage` instead. */
  progress: { current: number; total: number } | null;
  crawlComplete: CrawlComplete | null;
  currentPage: PageGenProgress | null;
  completedPages: string[];
  failedPages: string[];
  /** Persistent terminal failure text. Survives until the next run. */
  error: string | null;
  /** `Date.now()` when the run started; drives the elapsed timer. */
  startedAt: number | null;
  isLoading: boolean;
}

export interface StartCrawlParams {
  url: string;
  stack: string;
  maxPages: number;
  maxDepth: number;
  generateDatabase: boolean;
  generateAuth: boolean;
  /** Pack every page into one HTML file (one model call, no folder needed). */
  singleFile: boolean;
  settings: Settings;
}

const IDLE_STATE: CrawlRunState = {
  phase: "idle",
  status: "",
  progress: null,
  crawlComplete: null,
  currentPage: null,
  completedPages: [],
  failedPages: [],
  error: null,
  startedAt: null,
  isLoading: false,
};

/**
 * Drives a url-to-code run over a websocket.
 *
 * State is held in React state (not a mutated ref) so every consumer
 * re-renders on change without needing a manual subscribe/forceUpdate dance.
 */
export function useUrlToCode(
  onComplete: (code: Record<string, string>) => void
) {
  const [state, setState] = useState<CrawlRunState>(IDLE_STATE);
  const wsRef = useRef<WebSocket | null>(null);
  // Guards against a superseded socket's handlers mutating the current run.
  const runIdRef = useRef(0);
  // Pages arrive one by one; keeping the latest set means a connection that
  // drops near the end still hands over the pages that were finished.
  const partialCodeRef = useRef<Record<string, string>>({});
  const onCompleteRef = useRef(onComplete);

  useEffect(() => {
    onCompleteRef.current = onComplete;
  }, [onComplete]);

  // Close the socket if the component unmounts mid-run, otherwise its
  // handlers keep firing into an unmounted tree.
  useEffect(() => {
    return () => {
      runIdRef.current += 1;
      wsRef.current?.close(USER_CLOSE_WEB_SOCKET_CODE);
      wsRef.current = null;
    };
  }, []);

  const reset = useCallback(() => {
    runIdRef.current += 1;
    wsRef.current?.close(USER_CLOSE_WEB_SOCKET_CODE);
    wsRef.current = null;
    setState(IDLE_STATE);
  }, []);

  const startCrawl = useCallback((params: StartCrawlParams) => {
    const {
      url,
      stack,
      maxPages,
      maxDepth,
      generateDatabase,
      generateAuth,
      singleFile,
      settings,
    } = params;

    // Supersede any previous run before its handlers can touch new state.
    runIdRef.current += 1;
    const runId = runIdRef.current;
    const isStale = () => runIdRef.current !== runId;

    wsRef.current?.close(USER_CLOSE_WEB_SOCKET_CODE);
    partialCodeRef.current = {};

    setState({
      ...IDLE_STATE,
      phase: "crawling",
      status: "Connecting...",
      startedAt: Date.now(),
      isLoading: true,
    });

    let ws: WebSocket;
    try {
      ws = new WebSocket(`${WS_BACKEND_URL}/url-to-code`);
    } catch {
      setState((s) => ({
        ...s,
        phase: "failed",
        isLoading: false,
        error: `Could not open a connection to ${WS_BACKEND_URL}. Is the backend running?`,
      }));
      return;
    }
    wsRef.current = ws;

    ws.addEventListener("open", () => {
      if (isStale()) return;
      setState((s) => ({ ...s, status: "Starting crawl..." }));
      ws.send(
        JSON.stringify({
          url,
          stack,
          maxPages,
          maxDepth,
          generateDatabase,
          generateAuth,
          singleFile,
          openRouterApiKey: settings.openRouterApiKey,
          openRouterModel: settings.openRouterModel,
          openAiApiKey: settings.openAiApiKey,
          openAiBaseURL: settings.openAiBaseURL,
          anthropicApiKey: settings.anthropicApiKey,
          geminiApiKey: settings.geminiApiKey,
          customProviderBaseUrl: settings.customProviderBaseUrl,
          customProviderApiKey: settings.customProviderApiKey,
          customProviderModel: settings.customProviderModel,
        })
      );
    });

    ws.addEventListener("message", (event: MessageEvent) => {
      if (isStale()) return;

      let response: {
        type?: string;
        value?: string;
        data?: Record<string, unknown>;
      };
      try {
        response = JSON.parse(event.data);
      } catch {
        return;
      }

      switch (response.type) {
        case "status":
          setState((s) => ({ ...s, status: response.value || s.status }));
          break;

        case "progress":
          setState((s) => ({
            ...s,
            status: response.value || s.status,
            progress: {
              current: Number(response.data?.current ?? 0),
              total: Number(response.data?.total ?? 0),
            },
          }));
          break;

        case "crawlComplete": {
          const found = Number(response.data?.pagesFound ?? 0);
          const pages = (response.data?.pages ?? []) as PageInfo[];
          setState((s) => ({
            ...s,
            phase: "generating",
            progress: null,
            crawlComplete: { pagesFound: found, pages },
          }));
          if (found > 0) {
            toast.success(`Found ${found} ${found === 1 ? "page" : "pages"}`);
          }
          break;
        }

        case "pageStart":
          setState((s) => ({
            ...s,
            phase: "generating",
            currentPage: response.data as unknown as PageGenProgress,
          }));
          break;

        case "pageComplete": {
          const page = response.data as unknown as PageGenProgress;
          setState((s) => ({
            ...s,
            currentPage: null,
            completedPages: page.error
              ? s.completedPages
              : [...s.completedPages, page.path],
            failedPages: page.error
              ? [...s.failedPages, page.path]
              : s.failedPages,
          }));
          break;
        }

        case "partialCode": {
          const partial = (response.data?.code ?? {}) as Record<string, string>;
          if (Object.keys(partial).length > 0) {
            partialCodeRef.current = partial;
          }
          break;
        }

        case "setCode": {
          const code = (response.data?.code ?? {}) as Record<string, string>;
          const pageCount = Object.keys(code).length;
          if (pageCount === 0) {
            setState((s) => ({
              ...s,
              phase: "failed",
              isLoading: false,
              currentPage: null,
              error: "The model returned no code. Try again or pick a different model.",
            }));
            return;
          }
          setState((s) => ({
            ...s,
            phase: "done",
            isLoading: false,
            currentPage: null,
          }));
          onCompleteRef.current(code);
          break;
        }

        case "error":
          setState((s) => ({
            ...s,
            phase: "failed",
            isLoading: false,
            currentPage: null,
            error: response.value || "The run failed for an unknown reason.",
          }));
          break;
      }
    });

    // A close without a terminal message means the run died. Keep the user on
    // this screen with an explanation instead of silently resetting.
    ws.addEventListener("close", (event: CloseEvent) => {
      if (isStale()) return;
      wsRef.current = null;
      setState((s) => {
        if (!s.isLoading) return s;
        if (event.code === USER_CLOSE_WEB_SOCKET_CODE) {
          return { ...s, phase: "cancelled", isLoading: false, currentPage: null };
        }
        const salvaged = partialCodeRef.current;
        if (Object.keys(salvaged).length > 0) {
          // Deliver what was generated rather than discarding the run.
          onCompleteRef.current(salvaged);
          return {
            ...s,
            phase: "done",
            isLoading: false,
            currentPage: null,
            error:
              "The connection closed before the run finished; showing the " +
              `${Object.keys(salvaged).length} page(s) that were generated.`,
          };
        }
        return {
          ...s,
          phase: "failed",
          isLoading: false,
          currentPage: null,
          error:
            "The connection to the backend closed before the run finished. " +
            "Check the backend logs and try again.",
        };
      });
    });

    ws.addEventListener("error", () => {
      if (isStale()) return;
      setState((s) => ({
        ...s,
        phase: "failed",
        isLoading: false,
        currentPage: null,
        error: `Could not reach the backend at ${WS_BACKEND_URL}. Is it running?`,
      }));
    });
  }, []);

  const cancelCrawl = useCallback(() => {
    runIdRef.current += 1;
    wsRef.current?.close(USER_CLOSE_WEB_SOCKET_CODE);
    wsRef.current = null;
    setState((s) => ({
      ...s,
      phase: "cancelled",
      isLoading: false,
      currentPage: null,
    }));
    toast.success("Clone cancelled");
  }, []);

  return { state, startCrawl, cancelCrawl, reset };
}
