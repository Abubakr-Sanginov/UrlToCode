import { useRef, useCallback } from "react";
import { WS_BACKEND_URL } from "../config";
import { Settings } from "../types";
import toast from "react-hot-toast";

export interface CrawlProgress {
  status: string;
  current: number;
  total: number;
  phase: string;
}

export interface PageInfo {
  path: string;
  title: string;
  depth: number;
}

export interface CrawlComplete {
  pagesFound: number;
  siteStructure: any;
  designTokens: any;
  pages: PageInfo[];
}

export interface PageGenProgress {
  pageIndex: number;
  totalPages: number;
  path: string;
  codeLength?: number;
  error?: boolean;
}

interface UrlToCodeState {
  crawlProgress: CrawlProgress | null;
  crawlComplete: CrawlComplete | null;
  currentPage: PageGenProgress | null;
  completedPages: string[];
  errors: string[];
  isLoading: boolean;
}

export function useUrlToCode(
  onStart: () => void,
  onComplete: (code: Record<string, string>) => void,
  onError: () => void
) {
  const wsRef = useRef<WebSocket | null>(null);
  const stateRef = useRef<UrlToCodeState>({
    crawlProgress: null,
    crawlComplete: null,
    currentPage: null,
    completedPages: [],
    errors: [],
    isLoading: false,
  });
  const listenersRef = useRef<Set<() => void>>(new Set());

  const notify = useCallback(() => {
    listenersRef.current.forEach((fn) => fn());
  }, []);

  const subscribe = useCallback((fn: () => void) => {
    listenersRef.current.add(fn);
    return () => listenersRef.current.delete(fn);
  }, []);

  const getState = useCallback(() => stateRef.current, []);

  const startCrawl = useCallback(
    (
      url: string,
      stack: string,
      maxPages: number,
      maxDepth: number,
      generateDatabase: boolean,
      generateAuth: boolean,
      settings: Settings
    ) => {
      if (wsRef.current) {
        wsRef.current.close();
      }

      onStart();

      const s = stateRef.current;
      s.crawlProgress = null;
      s.crawlComplete = null;
      s.currentPage = null;
      s.completedPages = [];
      s.errors = [];
      s.isLoading = true;
      notify();

      const wsUrl = `${WS_BACKEND_URL}/url-to-code`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        ws.send(
          JSON.stringify({
            url,
            stack: stack.replace("-", "_"),
            maxPages,
            maxDepth,
            generateDatabase,
            generateAuth,
            openRouterApiKey: settings.openRouterApiKey,
            openRouterModel: settings.openRouterModel,
            openAiApiKey: settings.openAiApiKey,
            anthropicApiKey: settings.anthropicApiKey,
            geminiApiKey: settings.geminiApiKey,
          })
        );
      });

      ws.addEventListener("message", (event: MessageEvent) => {
        const response = JSON.parse(event.data);
        const st = stateRef.current;

        switch (response.type) {
          case "status":
            st.crawlProgress = {
              status: response.value || "",
              current: st.crawlProgress?.current || 0,
              total: st.crawlProgress?.total || 0,
              phase: response.data?.phase || "crawling",
            };
            notify();
            break;

          case "progress":
            st.crawlProgress = {
              status: response.value || "",
              current: response.data?.current || 0,
              total: response.data?.total || 0,
              phase: response.data?.phase || "crawling",
            };
            notify();
            break;

          case "crawlComplete":
            st.crawlComplete = response.data as CrawlComplete;
            st.crawlProgress = null;
            toast.success(`Found ${st.crawlComplete.pagesFound} pages!`);
            notify();
            break;

          case "pageStart":
            st.currentPage = response.data as PageGenProgress;
            notify();
            break;

          case "pageComplete": {
            const pageData = response.data as PageGenProgress;
            if (pageData.error) {
              st.errors = [...st.errors, pageData.path];
              toast.error(`Failed: ${pageData.path}`);
            } else {
              st.completedPages = [...st.completedPages, pageData.path];
            }
            st.currentPage = null;
            notify();
            break;
          }

          case "setCode":
            try {
              const codeData = JSON.parse(response.value || "{}");
              st.isLoading = false;
              notify();
              onComplete(codeData.code || {});
            } catch (e) {
              console.error("Failed to parse code data", e);
            }
            break;

          case "variantComplete":
            st.isLoading = false;
            toast.success("Full site generation complete!");
            notify();
            break;

          case "error":
            toast.error(response.value || "An error occurred");
            st.isLoading = false;
            notify();
            onError();
            break;
        }
      });

      ws.addEventListener("close", () => {
        const st = stateRef.current;
        if (st.isLoading) {
          st.isLoading = false;
          notify();
          onError();
        }
      });

      ws.addEventListener("error", () => {
        toast.error("WebSocket connection error");
        const st = stateRef.current;
        st.isLoading = false;
        notify();
        onError();
      });
    },
    [onStart, onComplete, onError, notify]
  );

  const cancelCrawl = useCallback(() => {
    wsRef.current?.close();
    const st = stateRef.current;
    st.isLoading = false;
    notify();
    onError();
    toast.success("Generation cancelled");
  }, [onError, notify]);

  return { startCrawl, cancelCrawl, subscribe, getState };
}
