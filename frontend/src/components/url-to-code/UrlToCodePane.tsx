import { useState } from "react";
import { LuGlobe2, LuLoader2, LuCheck, LuX, LuChevronDown, LuChevronUp } from "react-icons/lu";
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

interface Props {
  stack: Stack;
  setStack: (stack: Stack) => void;
  startCrawl: (
    url: string,
    stack: string,
    maxPages: number,
    maxDepth: number,
    generateDatabase: boolean,
    generateAuth: boolean,
    settings: Settings
  ) => void;
  cancelCrawl: () => void;
  getState: () => {
    crawlProgress: any;
    crawlComplete: any;
    currentPage: any;
    completedPages: string[];
    errors: string[];
    isLoading: boolean;
  };
  subscribe: (fn: () => void) => () => void;
  settings: Settings;
}

function stackLabel(stack: Stack): string {
  const desc = STACK_DESCRIPTIONS[stack];
  return desc.components.join(" + ");
}

function UrlToCodePane({ stack, setStack, startCrawl, cancelCrawl, getState, subscribe, settings }: Props) {
  const [url, setUrl] = useState("");
  const [maxPages, setMaxPages] = useState(30);
  const [maxDepth, setMaxDepth] = useState(4);
  const [generateDatabase, setGenerateDatabase] = useState(true);
  const [generateAuth, setGenerateAuth] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [, forceUpdate] = useState(0);

  subscribe(() => forceUpdate((n) => n + 1));

  const state = getState();

  function handleStart() {
    if (!url.trim()) {
      toast.error("Please enter a URL");
      return;
    }

    let finalUrl = url.trim();
    if (!finalUrl.startsWith("http://") && !finalUrl.startsWith("https://")) {
      finalUrl = "https://" + finalUrl;
    }

    startCrawl(finalUrl, stack, maxPages, maxDepth, generateDatabase, generateAuth, settings);
  }

  return (
    <div className="flex flex-col items-center gap-4">
      <div className="w-full max-w-2xl overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
        <div className="flex items-start gap-3 border-b border-gray-100 px-4 py-4 dark:border-zinc-800 sm:px-5">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-100 text-blue-600 dark:bg-blue-900/30 dark:text-blue-400">
            <LuGlobe2 className="h-4 w-4" />
          </span>
          <div>
            <h3 className="text-sm font-semibold text-gray-900 dark:text-zinc-100">
              Clone any website
            </h3>
            <p className="mt-0.5 text-xs leading-5 text-gray-500 dark:text-zinc-400">
              Paste a URL and AI will crawl &amp; recreate the entire site
            </p>
          </div>
        </div>

        <div className="space-y-3 px-4 py-4 sm:px-5">
          <label
            htmlFor="clone-url"
            className="block text-xs font-medium text-gray-600 dark:text-zinc-300"
          >
            Website URL
          </label>
          <Input
            id="clone-url"
            type="url"
            inputMode="url"
            autoComplete="url"
            placeholder="https://example.com"
            onChange={(e) => setUrl(e.target.value)}
            value={url}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !state.isLoading) {
                e.preventDefault();
                handleStart();
              }
            }}
            className="h-11 w-full"
            disabled={state.isLoading}
            data-testid="clone-url-input"
          />

          <div className="flex items-center gap-3">
            <label className="text-xs font-medium text-gray-600 dark:text-zinc-300">
              Stack:
            </label>
            <Select
              value={stack}
              onValueChange={(value) => setStack(value as Stack)}
              disabled={state.isLoading}
            >
              <SelectTrigger className="w-auto gap-2 font-medium" data-testid="stack-select">
                <SelectValue />
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

          <button
            onClick={() => setShowAdvanced(!showAdvanced)}
            className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 dark:text-zinc-400 dark:hover:text-zinc-200"
          >
            {showAdvanced ? (
              <LuChevronUp className="h-3 w-3" />
            ) : (
              <LuChevronDown className="h-3 w-3" />
            )}
            Advanced options
          </button>

          {showAdvanced && (
            <div className="space-y-2 rounded-lg bg-gray-50 p-3 dark:bg-zinc-800/50">
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-600 dark:text-zinc-300">
                    Max pages:
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={maxPages}
                    onChange={(e) => setMaxPages(parseInt(e.target.value) || 30)}
                    className="w-16 rounded-md border border-gray-200 px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                    disabled={state.isLoading}
                  />
                </div>
                <div className="flex items-center gap-2">
                  <label className="text-xs text-gray-600 dark:text-zinc-300">
                    Max depth:
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={maxDepth}
                    onChange={(e) => setMaxDepth(parseInt(e.target.value) || 4)}
                    className="w-16 rounded-md border border-gray-200 px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-200"
                    disabled={state.isLoading}
                  />
                </div>
              </div>
              <div className="flex items-center gap-4">
                <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-zinc-300">
                  <input
                    type="checkbox"
                    checked={generateDatabase}
                    onChange={(e) => setGenerateDatabase(e.target.checked)}
                    disabled={state.isLoading}
                    className="rounded"
                  />
                  Generate database schema
                </label>
                <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-zinc-300">
                  <input
                    type="checkbox"
                    checked={generateAuth}
                    onChange={(e) => setGenerateAuth(e.target.checked)}
                    disabled={state.isLoading}
                    className="rounded"
                  />
                  Generate auth system
                </label>
              </div>
            </div>
          )}
        </div>

        <div className="flex justify-end border-t border-gray-100 bg-gray-50/70 px-4 py-3.5 dark:border-zinc-800 dark:bg-zinc-800/50 sm:px-5">
          <div className="w-full sm:w-56">
            {state.isLoading ? (
              <button
                onClick={cancelCrawl}
                className="w-full rounded-lg bg-red-500 px-4 py-2.5 text-sm font-medium text-white hover:bg-red-600 transition-colors"
              >
                Cancel
              </button>
            ) : (
              <button
                onClick={handleStart}
                className="w-full rounded-lg bg-blue-500 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-600 transition-colors"
                data-testid="clone-start"
              >
                Clone Website
                <span className="ml-2 text-xs font-normal opacity-60">↵</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {(state.crawlProgress || state.crawlComplete || state.currentPage || state.completedPages.length > 0) && (
        <div className="w-full max-w-2xl overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
          <div className="border-b border-gray-100 px-4 py-3 dark:border-zinc-800">
            <h4 className="text-xs font-medium text-gray-700 dark:text-zinc-300">
              Progress
            </h4>
          </div>
          <div className="max-h-48 space-y-1.5 overflow-y-auto px-4 py-3">
            {state.crawlProgress && (
              <div className="flex items-center gap-2 text-xs text-gray-600 dark:text-zinc-400">
                <LuLoader2 className="h-3 w-3 animate-spin" />
                <span>{state.crawlProgress.status}</span>
                <span className="text-gray-400">
                  ({state.crawlProgress.current}/{state.crawlProgress.total})
                </span>
              </div>
            )}
            {state.crawlComplete && (
              <div className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400">
                <LuCheck className="h-3 w-3" />
                <span>Crawl complete: {state.crawlComplete.pagesFound} pages found</span>
              </div>
            )}
            {state.currentPage && (
              <div className="flex items-center gap-2 text-xs text-blue-600 dark:text-blue-400">
                <LuLoader2 className="h-3 w-3 animate-spin" />
                <span>
                  Generating: {state.currentPage.path} ({state.currentPage.pageIndex + 1}/
                  {state.currentPage.totalPages})
                </span>
              </div>
            )}
            {state.completedPages.length > 0 && (
              <div className="space-y-1">
                {state.completedPages.map((path) => (
                  <div
                    key={path}
                    className="flex items-center gap-2 text-xs text-green-600 dark:text-green-400"
                  >
                    <LuCheck className="h-3 w-3" />
                    <span>{path}</span>
                  </div>
                ))}
              </div>
            )}
            {state.errors.length > 0 && (
              <div className="space-y-1">
                {state.errors.map((path) => (
                  <div
                    key={path}
                    className="flex items-center gap-2 text-xs text-red-600 dark:text-red-400"
                  >
                    <LuX className="h-3 w-3" />
                    <span>{path}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default UrlToCodePane;
