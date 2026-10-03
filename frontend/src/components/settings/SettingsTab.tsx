import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import toast from "react-hot-toast";
import { BsCheckCircleFill, BsExclamationTriangleFill } from "react-icons/bs";
import { AppTheme, EditorTheme, Settings } from "../../types";
import { capitalize } from "../../lib/utils";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "../ui/select";
import { Input } from "../ui/input";
import { Button } from "../ui/button";
import { HTTP_BACKEND_URL } from "../../config";
import {
  CloneCacheInfo,
  clearCloneCache,
  describeCacheSize,
  fetchCloneCache,
} from "../../lib/cloneCache";

interface Props {
  settings: Settings;
  setSettings: React.Dispatch<React.SetStateAction<Settings>>;
  appTheme: AppTheme;
  setAppTheme: React.Dispatch<React.SetStateAction<AppTheme>>;
}

interface OpenRouterModel {
  id: string;
  name: string;
  description?: string;
}

interface OpenRouterModelResponse {
  id?: unknown;
  name?: unknown;
  description?: unknown;
}

/**
 * Starting suggestions for the model picker; typing searches OpenRouter's live
 * catalog instead.
 *
 * OpenRouter retires free tiers without removing the ":free" id from its
 * catalog, and a retired id fails every request with a 404 that reads like a
 * generic model error. These were verified to answer; if one starts returning
 * 404 "unavailable for free", replace it rather than leaving it here.
 */
const POPULAR_MODELS: OpenRouterModel[] = [
  {
    id: "inclusionai/ling-3.0-flash-fin:free",
    name: "Ling 3.0 Flash (Free)",
  },
  { id: "minimax/minimax-m3:free", name: "MiniMax M3 (Free)" },
  {
    id: "dots-studio/dots-3-note-preview:free",
    name: "Dots 3 Note Preview (Free)",
  },
  { id: "anthropic/claude-sonnet-4", name: "Claude Sonnet 4" },
  { id: "openai/gpt-4o", name: "GPT-4o" },
  { id: "openai/gpt-4o-mini", name: "GPT-4o Mini" },
  { id: "google/gemini-2.5-pro-preview", name: "Gemini 2.5 Pro" },
  { id: "deepseek/deepseek-r1", name: "DeepSeek R1" },
];

const CARD = "rounded-xl border border-border bg-card shadow-card";
const CARD_HEADER = "border-b border-border px-4 py-3";
const CARD_TITLE = "text-sm font-medium text-foreground";
const CARD_SUBTITLE = "mt-1 text-xs leading-5 text-muted-foreground";
const FIELD_LABEL = "text-sm font-medium text-foreground";
const FIELD_HINT = "mt-1 text-xs leading-5 text-muted-foreground";

/**
 * Mirrors `_get_model_for_provider` in backend/routes/url_to_code.py. Shown in
 * the UI so the precedence is not a surprise: a configured custom provider or
 * OpenRouter key wins over direct provider keys.
 */
function activeProviderLabel(settings: Settings): string | null {
  if (settings.customProviderBaseUrl) return "Custom provider";
  if (settings.openRouterApiKey) return "OpenRouter";
  if (settings.anthropicApiKey) return "Anthropic";
  if (settings.openAiApiKey) return "OpenAI";
  if (settings.geminiApiKey) return "Gemini";
  return null;
}

function SettingsTab({ settings, setSettings, appTheme, setAppTheme }: Props) {
  const [screenshotPreviewAvailable, setScreenshotPreviewAvailable] = useState<
    boolean | null
  >(null);
  const [openRouterModels, setOpenRouterModels] =
    useState<OpenRouterModel[]>(POPULAR_MODELS);
  const [openRouterSearch, setOpenRouterSearch] = useState("");
  const [isSearchingModels, setIsSearchingModels] = useState(false);
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const [activeOptionIndex, setActiveOptionIndex] = useState(0);
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [dropdownPos, setDropdownPos] = useState<{ top: number; left: number; width: number } | null>(null);
  // The generation cache. Shown because a cache the user cannot see or
  // empty is a cache that starts looking like the tool is wrong: the same
  // site producing a different page needs an explanation they can act on.
  const [pageCache, setPageCache] = useState<CloneCacheInfo | null>(null);
  const [isClearingCache, setIsClearingCache] = useState(false);

  const ids = useId();
  const listboxId = `${ids}-model-listbox`;
  const openRouterKeyId = `${ids}-openrouter-key`;
  const modelSearchId = `${ids}-model-search`;
  const reasoningEffortId = `${ids}-reasoning-effort`;
  const anthropicKeyId = `${ids}-anthropic-key`;
  const openAiKeyId = `${ids}-openai-key`;
  const geminiKeyId = `${ids}-gemini-key`;
  const customUrlId = `${ids}-custom-url`;
  const customKeyId = `${ids}-custom-key`;
  const customModelId = `${ids}-custom-model`;

  const activeProvider = activeProviderLabel(settings);

  useEffect(() => {
    let cancelled = false;
    fetch(`${HTTP_BACKEND_URL}/api/capabilities`)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!cancelled && data && typeof data.screenshot_preview === "boolean") {
          setScreenshotPreviewAvailable(data.screenshot_preview);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node;
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(target) &&
        !(target instanceof Element && target.closest(`#${listboxId}`))
      ) {
        setShowModelDropdown(false);
      }
    }
    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [listboxId]);

  // A pending debounce or in-flight search must not settle after unmount.
  useEffect(() => {
    return () => {
      if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
      searchAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchCloneCache()
      .then((info) => {
        if (!cancelled) setPageCache(info);
      })
      .catch(() => {
        // A backend that has never been asked for a cache still answers
        // with an empty one, so a failure here means the endpoint is not
        // there. Nothing about the rest of Settings depends on it.
        if (!cancelled) setPageCache(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function emptyPageCache() {
    setIsClearingCache(true);
    try {
      const result = await clearCloneCache();
      setPageCache((previous) => ({
        ...(previous ?? { entries: 0, bytes: 0, oldest: 0, directory: "" }),
        entries: 0,
        bytes: 0,
        oldest: 0,
      }));
      toast.success(
        result.removed
          ? `Cleared ${result.removed} cached page${result.removed === 1 ? "" : "s"}.`
          : "The cache was already empty."
      );
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "The cache could not be cleared.",
        { duration: 6000 }
      );
    } finally {
      setIsClearingCache(false);
    }
  }

  // The list is fixed-positioned in a portal, so it has to follow the input
  // whenever the panel scrolls or the layout moves; measuring once on open
  // left it hanging far below the field.
  useEffect(() => {
    if (!showModelDropdown) return;
    function place() {
      if (!inputRef.current) return;
      const rect = inputRef.current.getBoundingClientRect();
      setDropdownPos({ top: rect.bottom + 4, left: rect.left, width: rect.width });
    }
    place();
    // Capture: scrolling happens in an inner container, which never bubbles.
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    const frame = window.setInterval(place, 250);
    return () => {
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
      window.clearInterval(frame);
    };
  }, [showModelDropdown]);

  async function searchOpenRouterModels(query: string) {
    if (!settings.openRouterApiKey) return;
    if (query.trim().length < 2) {
      setOpenRouterModels(POPULAR_MODELS);
      return;
    }

    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;

    setIsSearchingModels(true);
    try {
      const response = await fetch(
        `https://openrouter.ai/api/v1/models?search=${encodeURIComponent(query)}`,
        {
          headers: { Authorization: `Bearer ${settings.openRouterApiKey}` },
          signal: controller.signal,
        }
      );
      if (response.ok) {
        const payload = (await response.json()) as {
          data?: OpenRouterModelResponse[];
        };
        const models: OpenRouterModel[] = (payload.data ?? [])
          .slice(0, 20)
          .filter((model): model is { id: string } & OpenRouterModelResponse =>
            typeof model.id === "string"
          )
          .map((model) => ({
            id: model.id,
            name: typeof model.name === "string" ? model.name : model.id,
            description:
              typeof model.description === "string" ? model.description : undefined,
          }));
        setOpenRouterModels(models);
      }
    } catch (error) {
      if ((error as Error).name !== "AbortError") {
        console.error("Failed to search OpenRouter models", error);
      }
    } finally {
      if (!controller.signal.aborted) setIsSearchingModels(false);
    }
  }

  function handleOpenRouterSearchChange(value: string) {
    setOpenRouterSearch(value);
    setActiveOptionIndex(0);
    if (searchTimeoutRef.current) clearTimeout(searchTimeoutRef.current);
    searchTimeoutRef.current = setTimeout(() => {
      searchOpenRouterModels(value);
    }, 300);
  }

  function selectModel(modelId: string) {
    setSettings((s) => ({ ...s, openRouterModel: modelId }));
    setShowModelDropdown(false);
    setOpenRouterSearch("");
  }

  const filteredModels = useMemo(() => {
    const query = openRouterSearch.trim().toLowerCase();
    if (!query) return openRouterModels;
    return openRouterModels.filter(
      (model) =>
        model.id.toLowerCase().includes(query) ||
        model.name.toLowerCase().includes(query)
    );
  }, [openRouterModels, openRouterSearch]);

  function handleSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setShowModelDropdown(false);
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!showModelDropdown) {
        setShowModelDropdown(true);
        return;
      }
      if (filteredModels.length === 0) return;
      setActiveOptionIndex((index) => {
        const delta = event.key === "ArrowDown" ? 1 : -1;
        return (index + delta + filteredModels.length) % filteredModels.length;
      });
      return;
    }
    if (event.key === "Enter" && showModelDropdown) {
      const model = filteredModels[activeOptionIndex];
      if (model) {
        event.preventDefault();
        selectModel(model.id);
      }
    }
  }

  const selectedModelName = settings.openRouterModel
    ? POPULAR_MODELS.find((m) => m.id === settings.openRouterModel)?.name ||
      settings.openRouterModel
    : "Not selected";

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-4 py-6 lg:px-6 lg:py-8">
        <div className="mx-auto max-w-lg space-y-5">
          <div>
            <h1 className="text-base font-semibold tracking-tight text-foreground">
              Settings
            </h1>
            <p className="mt-1 text-xs text-muted-foreground">
              Keys are stored in this browser only.
            </p>
          </div>

          {/* Active provider */}
          <div
            className={`rounded-xl border px-4 py-3 ${
              activeProvider
                ? "border-success-border bg-success-subtle"
                : "border-warning-border bg-warning-subtle"
            }`}
          >
            <div className="flex items-start gap-2.5">
              {activeProvider ? (
                <BsCheckCircleFill
                  className="mt-0.5 shrink-0 text-success"
                  aria-hidden="true"
                />
              ) : (
                <BsExclamationTriangleFill
                  className="mt-0.5 shrink-0 text-warning"
                  aria-hidden="true"
                />
              )}
              <div>
                <p
                  className={`text-sm font-medium ${
                    activeProvider ? "text-success" : "text-warning"
                  }`}
                >
                  {activeProvider
                    ? `Using ${activeProvider}`
                    : "No provider configured"}
                </p>
                <p
                  className={`mt-1 text-xs leading-5 ${
                    activeProvider ? "text-success/90" : "text-warning/90"
                  }`}
                >
                  {activeProvider
                    ? "Precedence: custom provider, OpenRouter, Anthropic, OpenAI, Gemini."
                    : "Add at least one API key below to run a clone."}
                </p>
              </div>
            </div>
          </div>

          {/* Theme */}
          <div className={CARD}>
            <div className={CARD_HEADER}>
              <h2 className={CARD_TITLE}>Appearance</h2>
            </div>
            <div className="divide-y divide-border">
              <div className="flex items-center justify-between px-4 py-3">
                <span className="text-sm text-foreground">App theme</span>
                <Select
                  name="app-theme"
                  value={appTheme}
                  onValueChange={(value) => setAppTheme(value as AppTheme)}
                >
                  <SelectTrigger className="w-[140px]" aria-label="App theme">
                    {capitalize(appTheme)}
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={AppTheme.SYSTEM}>System</SelectItem>
                    <SelectItem value={AppTheme.LIGHT}>Light</SelectItem>
                    <SelectItem value={AppTheme.DARK}>Dark</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between px-4 py-3">
                <span className="text-sm text-foreground">Editor theme</span>
                <Select
                  name="editor-theme"
                  value={settings.editorTheme}
                  onValueChange={(value) =>
                    setSettings((s) => ({
                      ...s,
                      editorTheme: value as EditorTheme,
                    }))
                  }
                >
                  <SelectTrigger
                    className="w-[140px]"
                    aria-label="Code editor theme"
                  >
                    <span className="notranslate" translate="no">
                      {capitalize(settings.editorTheme)}
                    </span>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cobalt">
                      <span className="notranslate" translate="no">
                        Cobalt
                      </span>
                    </SelectItem>
                    <SelectItem value="espresso">
                      <span className="notranslate" translate="no">
                        Espresso
                      </span>
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          {/* OpenRouter */}
          <div className={CARD}>
            <div className={CARD_HEADER}>
              <h2 className={CARD_TITLE}>OpenRouter</h2>
              <p className={CARD_SUBTITLE}>
                One key for 100+ models, including free ones
              </p>
            </div>
            <div className="space-y-4 p-4">
              <div>
                <label htmlFor={openRouterKeyId} className={FIELD_LABEL}>
                  API key
                </label>
                <p className={FIELD_HINT}>
                  Create one at{" "}
                  <a
                    href="https://openrouter.ai/keys"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-brand underline-offset-2 hover:underline"
                  >
                    openrouter.ai/keys
                  </a>
                </p>
                <Input
                  id={openRouterKeyId}
                  className="mt-2"
                  type="password"
                  autoComplete="off"
                  placeholder="sk-or-..."
                  value={settings.openRouterApiKey || ""}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      openRouterApiKey: e.target.value || null,
                    }))
                  }
                />
              </div>

              {settings.openRouterApiKey && (
                <div ref={dropdownRef}>
                  <label htmlFor={modelSearchId} className={FIELD_LABEL}>
                    Model
                  </label>
                  <p className={FIELD_HINT}>
                    Current:{" "}
                    <span className="font-medium text-foreground">
                      {selectedModelName}
                    </span>
                  </p>
                  <div className="mt-2">
                    <Input
                      id={modelSearchId}
                      ref={inputRef}
                      role="combobox"
                      aria-expanded={showModelDropdown}
                      aria-controls={listboxId}
                      aria-autocomplete="list"
                      aria-activedescendant={
                        showModelDropdown && filteredModels[activeOptionIndex]
                          ? `${listboxId}-${activeOptionIndex}`
                          : undefined
                      }
                      autoComplete="off"
                      placeholder="Search models..."
                      value={openRouterSearch}
                      onChange={(e) => {
                        handleOpenRouterSearchChange(e.target.value);
                        setShowModelDropdown(true);
                      }}
                      onFocus={() => setShowModelDropdown(true)}
                      onKeyDown={handleSearchKeyDown}
                    />
                    {showModelDropdown && dropdownPos && createPortal(
                      <div
                        id={listboxId}
                        role="listbox"
                        aria-label="OpenRouter models"
                        className="fixed z-[9999] max-h-96 overflow-y-auto rounded-xl border border-border bg-popover p-1.5 shadow-xl"
                        style={{ top: dropdownPos.top, left: dropdownPos.left, width: dropdownPos.width }}
                      >
                        {isSearchingModels && (
                          <div className="px-2 py-1.5 text-xs text-muted-foreground">
                            Searching...
                          </div>
                        )}
                        {filteredModels.map((model, index) => {
                          const isSelected =
                            settings.openRouterModel === model.id;
                          const isActive = index === activeOptionIndex;
                          return (
                            <button
                              key={model.id}
                              type="button"
                              id={`${listboxId}-${index}`}
                              role="option"
                              aria-selected={isSelected}
                              onMouseEnter={() => setActiveOptionIndex(index)}
                              onClick={() => selectModel(model.id)}
                              className={`w-full rounded-lg px-3 py-2.5 text-left text-sm transition-all ${
                                isActive
                                  ? "bg-brand/10 ring-1 ring-brand/20"
                                  : "hover:bg-muted/60"
                              } ${isSelected ? "bg-brand/5" : ""}`}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="min-w-0 truncate font-medium text-foreground">
                                  {model.name}
                                </span>
                                {isSelected && (
                                  <span className="shrink-0 rounded-full bg-brand-subtle px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand">
                                    Active
                                  </span>
                                )}
                              </div>
                              <div className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                                {model.id}
                              </div>
                            </button>
                          );
                        })}
                        {!isSearchingModels && filteredModels.length === 0 && (
                          <div className="px-2 py-1.5 text-xs text-muted-foreground">
                            No models found
                          </div>
                        )}
                      </div>,
                      document.body
                    )}
                  </div>

                  <label htmlFor={reasoningEffortId} className={`${FIELD_LABEL} mt-4`}>
                    Reasoning
                  </label>
                  <p className={FIELD_HINT}>
                    A reasoning model can spend its whole budget thinking and
                    return no page at all. Setting this to Low is what makes
                    those models usable here.
                  </p>
                  <select
                    id={reasoningEffortId}
                    className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                    value={settings.reasoningEffort || ""}
                    onChange={(e) =>
                      setSettings((s) => ({
                        ...s,
                        reasoningEffort: e.target.value || null,
                      }))
                    }
                  >
                    <option value="">Provider default</option>
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                  </select>
                </div>
              )}
            </div>
          </div>

          {/* Direct provider keys */}
          <div className={CARD}>
            <div className={CARD_HEADER}>
              <h2 className={CARD_TITLE}>Direct API keys</h2>
              <p className={CARD_SUBTITLE}>
                Used only when no custom provider or OpenRouter key is set
              </p>
            </div>
            <div className="space-y-4 p-4">
              <div>
                <label htmlFor={anthropicKeyId} className={FIELD_LABEL}>
                  Anthropic
                </label>
                <Input
                  id={anthropicKeyId}
                  className="mt-2"
                  type="password"
                  autoComplete="off"
                  placeholder="sk-ant-..."
                  value={settings.anthropicApiKey || ""}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      anthropicApiKey: e.target.value || null,
                    }))
                  }
                />
              </div>
              <div>
                <label htmlFor={openAiKeyId} className={FIELD_LABEL}>
                  OpenAI
                </label>
                <Input
                  id={openAiKeyId}
                  className="mt-2"
                  type="password"
                  autoComplete="off"
                  placeholder="sk-..."
                  value={settings.openAiApiKey || ""}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      openAiApiKey: e.target.value || null,
                    }))
                  }
                />
              </div>
              <div>
                <label htmlFor={geminiKeyId} className={FIELD_LABEL}>
                  Gemini
                </label>
                <Input
                  id={geminiKeyId}
                  className="mt-2"
                  type="password"
                  autoComplete="off"
                  placeholder="AIza..."
                  value={settings.geminiApiKey || ""}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      geminiApiKey: e.target.value || null,
                    }))
                  }
                />
              </div>
            </div>
          </div>

          {/* Custom OpenAI-compatible provider */}
          <div className={CARD}>
            <div className={CARD_HEADER}>
              <h2 className={CARD_TITLE}>Custom provider</h2>
              <p className={CARD_SUBTITLE}>
                Any OpenAI-compatible API — Ollama, vLLM, LM Studio. Takes
                precedence over every other key.
              </p>
            </div>
            <div className="space-y-4 p-4">
              <div>
                <label htmlFor={customUrlId} className={FIELD_LABEL}>
                  Base URL
                </label>
                <p className={FIELD_HINT}>
                  <code className="font-mono">http://localhost:11434/v1</code>{" "}
                  for Ollama,{" "}
                  <code className="font-mono">http://localhost:8000/v1</code> for
                  vLLM
                </p>
                <Input
                  id={customUrlId}
                  className="mt-2 font-mono text-xs"
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="http://localhost:11434/v1"
                  value={settings.customProviderBaseUrl || ""}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      customProviderBaseUrl: e.target.value.trim() || null,
                    }))
                  }
                />
              </div>
              <div>
                <label htmlFor={customKeyId} className={FIELD_LABEL}>
                  API key
                </label>
                <p className={FIELD_HINT}>
                  Optional — leave empty for local servers
                </p>
                <Input
                  id={customKeyId}
                  className="mt-2"
                  type="password"
                  autoComplete="off"
                  placeholder="sk-... or leave empty"
                  value={settings.customProviderApiKey || ""}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      customProviderApiKey: e.target.value || null,
                    }))
                  }
                />
              </div>
              <div>
                <label htmlFor={customModelId} className={FIELD_LABEL}>
                  Model name
                </label>
                <p className={FIELD_HINT}>
                  As the server names it, e.g.{" "}
                  <code className="font-mono">llama3</code>
                </p>
                <Input
                  id={customModelId}
                  className="mt-2 font-mono text-xs"
                  type="text"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="llama3"
                  value={settings.customProviderModel || ""}
                  onChange={(e) =>
                    setSettings((s) => ({
                      ...s,
                      customProviderModel: e.target.value.trim() || null,
                    }))
                  }
                />
              </div>
              {settings.customProviderBaseUrl &&
                !settings.customProviderModel && (
                  <div className="flex items-start gap-2 rounded-lg border border-warning-border bg-warning-subtle p-2.5">
                    <BsExclamationTriangleFill
                      className="mt-0.5 shrink-0 text-warning"
                      aria-hidden="true"
                    />
                    <p className="text-xs leading-5 text-warning">
                      Set a model name, otherwise the request is sent with the
                      model <code className="font-mono">default</code>, which
                      most servers reject.
                    </p>
                  </div>
                )}
            </div>
          </div>

          {/* Screenshot Preview */}
          <div className={CARD}>
            <div className={CARD_HEADER}>
              <h2 className={CARD_TITLE}>Screenshot preview</h2>
              <p className={CARD_SUBTITLE}>
                Lets the model render and check its own output
              </p>
            </div>
            <div className="p-4">
              {screenshotPreviewAvailable === false ? (
                <div className="flex items-start gap-2.5 rounded-lg border border-warning-border bg-warning-subtle p-3">
                  <BsExclamationTriangleFill
                    className="mt-0.5 shrink-0 text-warning"
                    aria-hidden="true"
                  />
                  <div>
                    <p className="text-sm font-medium text-warning">
                      Unavailable
                    </p>
                    <p className="mt-1 text-xs leading-5 text-warning/90">
                      Install with{" "}
                      <code className="rounded bg-warning/10 px-1 py-0.5 font-mono">
                        playwright install chromium
                      </code>
                    </p>
                  </div>
                </div>
              ) : screenshotPreviewAvailable === true ? (
                <div className="flex items-center gap-2.5">
                  <BsCheckCircleFill
                    className="shrink-0 text-success"
                    aria-hidden="true"
                  />
                  <p className="text-sm text-foreground">Available</p>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">Checking...</p>
              )}
            </div>
          </div>

          {/* Generation cache */}
          <div className={CARD}>
            <div className={CARD_HEADER}>
              <h2 className={CARD_TITLE}>Generated page cache</h2>
              <p className={CARD_SUBTITLE}>
                Cloning the same site again reuses pages already generated
              </p>
            </div>
            <div className="p-4">
              {pageCache === null ? (
                <p className="text-xs text-muted-foreground">Checking...</p>
              ) : pageCache.entries === 0 ? (
                <div className="flex items-center gap-2.5">
                  <BsCheckCircleFill
                    className="shrink-0 text-success"
                    aria-hidden="true"
                  />
                  <p className="text-sm text-foreground">
                    Nothing cached — every page is generated from scratch
                  </p>
                </div>
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-foreground">
                    {pageCache.entries} page{pageCache.entries === 1 ? "" : "s"} kept
                    {pageCache.oldest
                      ? ` · oldest ${new Date(pageCache.oldest * 1000).toLocaleDateString()}`
                      : ""}
                    {` · ${describeCacheSize(pageCache.bytes)}`}
                  </p>
                  <Button
                    onClick={() => void emptyPageCache()}
                    variant="outline"
                    size="sm"
                    disabled={isClearingCache}
                    title="Generate every page from the site again next time, at full cost"
                    className="gap-1.5 text-xs"
                    data-testid="clear-page-cache"
                  >
                    {isClearingCache ? "Clearing..." : "Clear cache"}
                  </Button>
                </div>
              )}
              <p className="mt-2.5 text-xs leading-5 text-muted-foreground">
                A cached page is reused only for the same site, page, stack, model
                and prompt. Changing any of them generates it again.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default SettingsTab;
