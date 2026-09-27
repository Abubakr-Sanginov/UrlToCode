import React, { useState, useEffect, useRef } from "react";
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
import { HTTP_BACKEND_URL } from "../../config";

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
  pricing?: {
    prompt: string;
    completion: string;
  };
}

const POPULAR_MODELS: OpenRouterModel[] = [
  { id: "meta-llama/llama-3.3-70b-instruct:free", name: "Llama 3.3 70B (Free)" },
  { id: "meta-llama/llama-3.2-11b-vision-instruct:free", name: "Llama 3.2 11B Vision (Free)" },
  { id: "google/gemini-2.0-flash-exp:free", name: "Gemini 2.0 Flash (Free)" },
  { id: "qwen/qwen-2.5-72b-instruct:free", name: "Qwen 2.5 72B (Free)" },
  { id: "mistralai/mistral-small-3.1-24b-instruct:free", name: "Mistral Small 3.1 24B (Free)" },
  { id: "deepseek/deepseek-chat-v3-0324:free", name: "DeepSeek Chat v3 (Free)" },
  { id: "nvidia/llama-3.1-nemotron-70b-instruct:free", name: "Nemotron 70B (Free)" },
  { id: "anthropic/claude-sonnet-4", name: "Claude Sonnet 4" },
  { id: "openai/gpt-4o", name: "GPT-4o" },
  { id: "openai/gpt-4o-mini", name: "GPT-4o Mini" },
  { id: "google/gemini-2.5-pro-preview", name: "Gemini 2.5 Pro" },
  { id: "deepseek/deepseek-r1", name: "DeepSeek R1" },
];

function SettingsTab({ settings, setSettings, appTheme, setAppTheme }: Props) {
  const [screenshotPreviewAvailable, setScreenshotPreviewAvailable] = useState<
    boolean | null
  >(null);
  const [openRouterModels, setOpenRouterModels] = useState<OpenRouterModel[]>([]);
  const [openRouterSearch, setOpenRouterSearch] = useState("");
  const [isSearchingModels, setIsSearchingModels] = useState(false);
  const [showModelDropdown, setShowModelDropdown] = useState(false);
  const searchTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

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
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowModelDropdown(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function searchOpenRouterModels(query: string) {
    if (!settings.openRouterApiKey) return;
    if (query.length < 2) {
      setOpenRouterModels(POPULAR_MODELS);
      return;
    }

    setIsSearchingModels(true);
    try {
      const response = await fetch(
        `https://openrouter.ai/api/v1/models?search=${encodeURIComponent(query)}`,
        {
          headers: {
            Authorization: `Bearer ${settings.openRouterApiKey}`,
          },
        }
      );
      if (response.ok) {
        const data = await response.json();
        const models = (data.data || []).slice(0, 20).map((m: any) => ({
          id: m.id,
          name: m.name || m.id,
          description: m.description,
          pricing: m.pricing,
        }));
        setOpenRouterModels(models);
      }
    } catch (e) {
      console.error("Failed to search OpenRouter models", e);
    } finally {
      setIsSearchingModels(false);
    }
  }

  function handleOpenRouterSearchChange(value: string) {
    setOpenRouterSearch(value);
    if (searchTimeoutRef.current) {
      clearTimeout(searchTimeoutRef.current);
    }
    searchTimeoutRef.current = setTimeout(() => {
      searchOpenRouterModels(value);
    }, 300);
  }

  function selectModel(modelId: string) {
    setSettings((s) => ({ ...s, openRouterModel: modelId }));
    setShowModelDropdown(false);
    setOpenRouterSearch("");
  }

  const filteredModels = openRouterSearch
    ? openRouterModels.filter(
        (m) =>
          m.id.toLowerCase().includes(openRouterSearch.toLowerCase()) ||
          m.name.toLowerCase().includes(openRouterSearch.toLowerCase())
      )
    : openRouterModels.length > 0
      ? openRouterModels
      : POPULAR_MODELS;

  const selectedModelName = settings.openRouterModel
    ? POPULAR_MODELS.find((m) => m.id === settings.openRouterModel)?.name ||
      settings.openRouterModel
    : "Not selected";

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-4 py-4 lg:px-6 lg:py-6">
        <div className="mb-6">
          <h1 className="text-lg font-semibold text-gray-900 dark:text-white">
            Settings
          </h1>
        </div>

        <div className="mx-auto max-w-lg space-y-6">
          {/* Theme */}
          <div className="rounded-lg border border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-800/60">
            <div className="border-b border-gray-100 px-4 py-3 dark:border-zinc-700">
              <h2 className="text-sm font-medium text-gray-900 dark:text-white">
                Theme
              </h2>
            </div>
            <div className="divide-y divide-gray-100 dark:divide-zinc-700">
              <div className="flex items-center justify-between px-4 py-3">
                <div>
                  <span className="text-sm text-gray-700 dark:text-zinc-300">
                    App Theme
                  </span>
                </div>
                <Select
                  name="app-theme"
                  value={appTheme}
                  onValueChange={(value) => setAppTheme(value as AppTheme)}
                >
                  <SelectTrigger className="w-[140px]">
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
                <div>
                  <span className="text-sm text-gray-700 dark:text-zinc-300">
                    Code Editor Theme
                  </span>
                </div>
                <Select
                  name="editor-theme"
                  value={settings.editorTheme}
                  onValueChange={(value) =>
                    setSettings((s) => ({ ...s, editorTheme: value as EditorTheme }))
                  }
                >
                  <SelectTrigger className="w-[140px]">
                    <span className="notranslate" translate="no">
                      {capitalize(settings.editorTheme)}
                    </span>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="cobalt">
                      <span className="notranslate" translate="no">Cobalt</span>
                    </SelectItem>
                    <SelectItem value="espresso">
                      <span className="notranslate" translate="no">Espresso</span>
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
          </div>

          {/* OpenRouter */}
          <div className="rounded-lg border border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-800/60">
            <div className="border-b border-gray-100 px-4 py-3 dark:border-zinc-700">
              <h2 className="text-sm font-medium text-gray-900 dark:text-white">
                OpenRouter API
              </h2>
              <p className="mt-1 text-xs text-gray-500 dark:text-zinc-400">
                Access 100+ models through OpenRouter
              </p>
            </div>
            <div className="space-y-4 p-4">
              <div>
                <p className="text-sm font-medium text-gray-700 dark:text-zinc-300">
                  API Key
                </p>
                <p className="mt-1 text-xs text-gray-500 dark:text-zinc-400">
                  Get your key at{" "}
                  <a
                    href="https://openrouter.ai/keys"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-violet-600 hover:text-violet-700 dark:text-violet-400"
                  >
                    openrouter.ai/keys
                  </a>
                </p>
                <Input
                  id="openrouter-api-key"
                  className="mt-2"
                  type="password"
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
                  <p className="text-sm font-medium text-gray-700 dark:text-zinc-300">
                    Model
                  </p>
                  <p className="mt-1 text-xs text-gray-500 dark:text-zinc-400">
                    Current: <span className="font-medium text-gray-700 dark:text-zinc-200">{selectedModelName}</span>
                  </p>
                  <div className="relative mt-2">
                    <Input
                      placeholder="Search models..."
                      value={openRouterSearch}
                      onChange={(e) => {
                        handleOpenRouterSearchChange(e.target.value);
                        setShowModelDropdown(true);
                      }}
                      onFocus={() => {
                        setShowModelDropdown(true);
                        if (openRouterModels.length === 0) {
                          setOpenRouterModels(POPULAR_MODELS);
                        }
                      }}
                    />
                    {showModelDropdown && (
                      <div className="absolute z-50 mt-1 w-full max-h-64 overflow-y-auto rounded-lg border border-gray-200 bg-white shadow-lg dark:border-zinc-700 dark:bg-zinc-800">
                        {isSearchingModels && (
                          <div className="px-3 py-2 text-xs text-gray-500 dark:text-zinc-400">
                            Searching...
                          </div>
                        )}
                        {filteredModels.map((model) => (
                          <button
                            key={model.id}
                            onClick={() => selectModel(model.id)}
                            className={`w-full px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-zinc-700 ${
                              settings.openRouterModel === model.id
                                ? "bg-violet-50 dark:bg-violet-900/20"
                                : ""
                            }`}
                          >
                            <div className="font-medium text-gray-900 dark:text-zinc-100">
                              {model.name}
                            </div>
                            <div className="text-xs text-gray-500 dark:text-zinc-400 truncate">
                              {model.id}
                            </div>
                          </button>
                        ))}
                        {!isSearchingModels && filteredModels.length === 0 && (
                          <div className="px-3 py-2 text-xs text-gray-500 dark:text-zinc-400">
                            No models found
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Other API Keys */}
          <div className="rounded-lg border border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-800/60">
            <div className="border-b border-gray-100 px-4 py-3 dark:border-zinc-700">
              <h2 className="text-sm font-medium text-gray-900 dark:text-white">
                Other API Keys (Optional)
              </h2>
              <p className="mt-1 text-xs text-gray-500 dark:text-zinc-400">
                Direct API keys (overrides OpenRouter)
              </p>
            </div>
            <div className="space-y-4 p-4">
              <div>
                <p className="text-sm font-medium text-gray-700 dark:text-zinc-300">
                  Anthropic API Key
                </p>
                <Input
                  className="mt-2"
                  type="password"
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
                <p className="text-sm font-medium text-gray-700 dark:text-zinc-300">
                  OpenAI API Key
                </p>
                <Input
                  className="mt-2"
                  type="password"
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
                <p className="text-sm font-medium text-gray-700 dark:text-zinc-300">
                  Gemini API Key
                </p>
                <Input
                  className="mt-2"
                  type="password"
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

          {/* Screenshot Preview */}
          <div className="rounded-lg border border-gray-200 bg-white dark:border-zinc-700 dark:bg-zinc-800/60">
            <div className="border-b border-gray-100 px-4 py-3 dark:border-zinc-700">
              <h2 className="text-sm font-medium text-gray-900 dark:text-white">
                Screenshot Preview
              </h2>
            </div>
            <div className="p-4">
              {screenshotPreviewAvailable === false ? (
                <div className="flex items-start gap-2.5 rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-700/60 dark:bg-amber-900/20">
                  <BsExclamationTriangleFill className="mt-0.5 shrink-0 text-amber-500" />
                  <div>
                    <p className="text-sm font-medium text-amber-800 dark:text-amber-200">
                      Screenshot preview unavailable
                    </p>
                    <p className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      Install with{" "}
                      <code className="rounded bg-amber-100 px-1 py-0.5 font-mono dark:bg-amber-900/40">
                        playwright install chromium
                      </code>
                    </p>
                  </div>
                </div>
              ) : screenshotPreviewAvailable === true ? (
                <div className="flex items-start gap-2.5">
                  <BsCheckCircleFill className="mt-0.5 shrink-0 text-emerald-500" />
                  <p className="text-sm text-gray-700 dark:text-zinc-300">Available</p>
                </div>
              ) : (
                <p className="text-xs text-gray-500 dark:text-zinc-400">
                  Checking...
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default SettingsTab;
