import { useCallback, useEffect, useRef, useState } from "react";
import { WS_BACKEND_URL } from "../config";
import { USER_CLOSE_WEB_SOCKET_CODE } from "../constants";
import { Settings } from "../types";
import toast from "react-hot-toast";
import { useCloneStore } from "../store/clone-store";
import { GENERATED_FILE_PREFIX } from "../lib/projectFiles";
import { tokenForSocket, Usage } from "../lib/accounts";
import { useAccountStore } from "../store/account-store";

// Kept in sync with the backend clamps in routes/url_to_code.py so the UI
// cannot offer values the server would silently discard.
export const MAX_CRAWL_PAGES = 30;
export const MAX_CRAWL_DEPTH = 6;
export const MAX_RETRIES = 5;

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
  | "choosing"
  | "generating"
  | "done"
  | "failed"
  | "cancelled";

/** One page the crawl found, and the question of whether to build it. */
export interface SelectablePage {
  path: string;
  title: string;
  url: string;
  depth: number;
  screenshot?: string;
}

export interface PageSelection {
  pages: SelectablePage[];
  timeoutSeconds: number;
}

export interface CrawlRunState {
  phase: CrawlPhase;
  /** Latest coarse status line, e.g. "Generating page 3/12". */
  status: string;
  /** Crawl-phase counter. Page generation uses `currentPage` instead. */
  progress: { current: number; total: number } | null;
  crawlComplete: CrawlComplete | null;
  /** Set while the user is choosing which pages to build. */
  pageSelection: PageSelection | null;
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
  maxRetries: number;
  generateDatabase: boolean;
  generateAuth: boolean;
  /** Pack every page into one HTML file (one model call, no folder needed). */
  singleFile: boolean;
  /** Capture and check every page at phone, tablet and desktop widths. */
  responsive: boolean;
  /** Click the page's own controls and capture what they reveal. */
  captureInteractions: boolean;
  /** Show the pages the crawl found and let the user pick which to build. */
  confirmPages: boolean;
  /**
   * A hard limit in dollars for this run. Zero means no limit, which is
   * what an empty field arrives as - the run must not stop before it has
   * generated anything unless the user asked for that.
   */
  maxCost: number;
  settings: Settings;
}

const IDLE_STATE: CrawlRunState = {
  phase: "idle",
  status: "",
  progress: null,
  crawlComplete: null,
  pageSelection: null,
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
      maxRetries,
      generateDatabase,
      generateAuth,
      singleFile,
      responsive,
      captureInteractions,
      confirmPages,
      maxCost,
      settings,
    } = params;

    // Supersede any previous run before its handlers can touch new state.
    runIdRef.current += 1;
    const runId = runIdRef.current;
    const isStale = () => runIdRef.current !== runId;

    wsRef.current?.close(USER_CLOSE_WEB_SOCKET_CODE);
    partialCodeRef.current = {};
    useCloneStore.getState().resetCloneSource();

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
          maxRetries,
          generateDatabase,
          generateAuth,
          singleFile,
          responsive,
          captureInteractions,
          confirmPages,
          maxCost,
          openRouterApiKey: settings.openRouterApiKey,
          openRouterModel: settings.openRouterModel,
          reasoningEffort: settings.reasoningEffort,
          openAiApiKey: settings.openAiApiKey,
          openAiBaseURL: settings.openAiBaseURL,
          anthropicApiKey: settings.anthropicApiKey,
          geminiApiKey: settings.geminiApiKey,
          customProviderBaseUrl: settings.customProviderBaseUrl,
          customProviderApiKey: settings.customProviderApiKey,
          customProviderModel: settings.customProviderModel,
          // A browser will not put a cookie on a websocket handshake to
          // another origin, and there is no flag that changes it, so the
          // session travels in this first message instead.
          sessionToken: tokenForSocket(),
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
          useCloneStore.getState().setCloneSource({
            baseUrl: (response.data?.baseUrl as string | undefined) ?? null,
            runId: (response.data?.runId as string | undefined) ?? null,
            screenshots: (response.data?.screenshots ?? {}) as Record<string, string>,
            sourceUrls: (response.data?.sourceUrls ?? {}) as Record<string, string>,
          });
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

        case "pageSelection": {
          // The server has the crawl and is waiting. Nothing is generated
          // until this is answered, so the UI simply holds the question.
          const pages = (response.data?.pages ?? []) as SelectablePage[];
          const timeoutSeconds = Number(response.data?.timeoutSeconds ?? 600);
          setState((s) => ({
            ...s,
            phase: "choosing",
            status: response.value ?? "Choose which pages to build",
            pageSelection: { pages, timeoutSeconds },
          }));
          break;
        }

        case "pageStart":
          setState((s) => ({
            ...s,
            phase: "generating",
            pageSelection: null,
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
          // The server and mock files come in the same map as the pages,
          // keyed `file:server/app.py`. They are separated out here so they
          // reach the project as files without becoming versions a visitor
          // could switch to.
          const generatedFiles: Record<string, string> = {};
          for (const [key, content] of Object.entries(code)) {
            if (key.startsWith(GENERATED_FILE_PREFIX)) {
              generatedFiles[key.slice(GENERATED_FILE_PREFIX.length)] = content;
            }
          }
          if (Object.keys(generatedFiles).length > 0) {
            useCloneStore.setState({ generatedFiles });
          }
          // A finished clone becomes one of the account's projects, and the
          // server says so in the same message. Taken from here rather than
          // left to the next poll, or the panel would still read "0 of 1"
          // the moment after telling the user the clone worked.
          const settled = response.data?.usage as Usage | undefined;
          if (settled) {
            useAccountStore.setState({ usage: settled });
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

  /**
   * Answer the page-selection question.
   *
   * An empty list is a real answer: the user looked at the pages and chose
   * none of them. The server takes that as a decision and stops, which is
   * why this does not check for emptiness and quietly send everything.
   */
  const choosePages = useCallback((paths: string[]) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      toast.error("The connection to the backend is already gone.");
      return;
    }
    setState((s) => ({
      ...s,
      phase: paths.length ? "generating" : "cancelled",
      pageSelection: null,
      isLoading: paths.length > 0,
      status: paths.length
        ? `Generating ${paths.length} selected page${paths.length === 1 ? "" : "s"}...`
        : "No pages selected",
    }));
    try {
      ws.send(JSON.stringify({ type: "pageSelection", paths }));
    } catch {
      toast.error("The selection could not be sent to the backend.");
    }
  }, []);

  return { state, startCrawl, cancelCrawl, choosePages, reset };
}
