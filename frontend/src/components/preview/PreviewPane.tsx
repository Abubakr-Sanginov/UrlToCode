import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";
import {
  FaDesktop,
  FaMobile,
  FaCode,
} from "react-icons/fa";
import {
  LuChevronLeft,
  LuChevronRight,
  LuFolderOpen,
  LuRefreshCw,
  LuDownload,
  LuLoader2,
  LuCode,
} from "react-icons/lu";
import { useMemo, useState, useCallback } from "react";
import toast from "react-hot-toast";
import { AppState, Settings } from "../../types";
import CodeTab from "./CodeTab";
import CodeEditorPane from "../editor/CodeEditorPane";
import { Button } from "../ui/button";
import { useAppStore } from "../../store/app-store";
import { useProjectStore } from "../../store/project-store";
import { extractHtml } from "./extractHtml";
import PreviewComponent from "./PreviewComponent";
import { downloadCode } from "./download";
import { SelectAndEditToolbarButton } from "../select-and-edit/SelectAndEditControls";
import { normalizeBabelCdn } from "../../lib/babelCdn";
import ImageScanningPreview from "./ImageScanningPreview";
import {
  FolderPickCancelled,
  buildCodeMapFromCommits,
  saveAndRunProject,
  siteNameFromCode,
} from "../../lib/localProject";

function prepareHtmlForNewTab(code: string) {
  const html = normalizeBabelCdn(code);
  if (/<base\s/i.test(html)) return html;

  const baseTag = `<base href="${window.location.origin}/">`;
  return html.replace(/<head(\s[^>]*)?>/i, (match) => `${match}${baseTag}`);
}

function openInNewTab(code: string) {
  const blob = new Blob([prepareHtmlForNewTab(code)], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

interface Props {
  settings: Settings;
  onOpenVersions: () => void;
}

function PreviewPane({ settings, onOpenVersions }: Props) {
  const { appState } = useAppStore();
  const { inputMode, head, commits, setHead } = useProjectStore();
  const [activeTab, setActiveTab] = useState("desktop");
  const [desktopScale, setDesktopScale] = useState(1);
  const [desktopViewMode, setDesktopViewMode] = useState<"fit" | "actual">("fit");
  const [isSavingProject, setIsSavingProject] = useState(false);
  const [activeEditorFile, setActiveEditorFile] = useState("index.html");

  const sortedCommits = useMemo(() =>
    Object.values(commits).sort(
      (a, b) => new Date(a.dateCreated).getTime() - new Date(b.dateCreated).getTime()
    ), [commits]);

  const currentVersionIndex = sortedCommits.findIndex(c => c.hash === head);
  const totalVersions = sortedCommits.length;
  const canGoPrev = currentVersionIndex > 0;
  const canGoNext = currentVersionIndex < totalVersions - 1;

  const currentCommit = head && commits[head] ? commits[head] : "";
  const selectedVariant = currentCommit
    ? currentCommit.variants[currentCommit.selectedVariantIndex] ??
      currentCommit.variants[0]
    : undefined;
  const currentCode = selectedVariant ? selectedVariant.code : "";
  const currentLabel = currentCommit ? currentCommit.label : undefined;

  const isSelectedVariantComplete =
    !!selectedVariant && selectedVariant.status === "complete";

  const previewCode =
    inputMode === "video" && appState === AppState.CODING
      ? extractHtml(currentCode)
      : currentCode;
  const sourceImage = currentCommit ? currentCommit.inputs?.images[0] : undefined;
  const showImageScanningPreview =
    appState === AppState.CODING &&
    currentCommit !== "" &&
    currentCommit.type === "ai_create" &&
    inputMode === "image" &&
    !previewCode.trim() &&
    !!sourceImage;

  const canSelectAndEdit =
    appState === AppState.CODE_READY || !!isSelectedVariantComplete;

  const fileMap = useMemo(() => {
    const files: Record<string, string> = {};
    const sorted = Object.values(commits).sort(
      (a, b) => new Date(a.dateCreated).getTime() - new Date(b.dateCreated).getTime()
    );
    for (const commit of sorted) {
      const variant = commit.variants[commit.selectedVariantIndex] || commit.variants[0];
      if (variant?.code) {
        const label = commit.label || (commit.type === "code_create" ? "imported" : `version-${commit.hash.slice(0, 6)}`);
        const fileName = label.includes("/") ? label : `${label}.html`;
        files[fileName] = variant.code;
      }
    }
    if (Object.keys(files).length === 0 && previewCode) {
      files["index.html"] = previewCode;
    }
    return files;
  }, [commits, previewCode]);

  const handleEditorCodeChange = useCallback((path: string, newCode: string) => {
    toast.success(`Saved changes to ${path} (${newCode.length} chars)`);
  }, []);

  const handleSaveAndRun = async () => {
    if (isSavingProject) return;

    const committedCode = buildCodeMapFromCommits(commits);
    const projectCode =
      Object.keys(committedCode).length > 0 ? committedCode : { "/": previewCode };
    const hasContent = Object.values(projectCode).some(
      (value) => value && value.trim()
    );
    if (!hasContent) {
      toast.error("Wait for the code to finish generating first.");
      return;
    }

    setIsSavingProject(true);
    try {
      const result = await saveAndRunProject(
        projectCode,
        siteNameFromCode(projectCode)
      );
      if (result.savedToFolder) {
        toast.success(
          `Saved ${result.fileCount} file${result.fileCount === 1 ? "" : "s"} to "${result.folderName}"`
        );
      } else {
        toast.success(
          `Project saved and running (${result.fileCount} file${result.fileCount === 1 ? "" : "s"})`
        );
      }
      window.open(result.serveUrl, "_blank");
    } catch (error) {
      if (error instanceof FolderPickCancelled) {
        toast.error("Folder selection cancelled");
        return;
      }
      console.error("Save & run failed", error);
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not save and run the project."
      );
    } finally {
      setIsSavingProject(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex-1 flex flex-col min-h-0"
      >
        {/* Toolbar */}
        <div className="editor-toolbar relative flex shrink-0 items-center justify-between px-3 py-2 gap-2">
          <div className="flex items-center gap-3 min-w-0">
            <TabsList className="shrink-0 bg-muted/50 p-0.5">
              <TabsTrigger
                value="desktop"
                title="Desktop preview"
                aria-label="Desktop preview"
                data-testid="tab-desktop"
                className="editor-tab-trigger gap-1.5 px-3 py-1.5"
              >
                <FaDesktop aria-hidden="true" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Desktop</span>
              </TabsTrigger>
              <TabsTrigger
                value="mobile"
                title="Mobile preview"
                aria-label="Mobile preview"
                data-testid="tab-mobile"
                className="editor-tab-trigger gap-1.5 px-3 py-1.5"
              >
                <FaMobile aria-hidden="true" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Mobile</span>
              </TabsTrigger>
              <TabsTrigger
                value="code"
                title="Quick code view"
                data-testid="tab-code"
                className="editor-tab-trigger gap-1.5 px-3 py-1.5"
              >
                <FaCode aria-hidden="true" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Quick View</span>
              </TabsTrigger>
              <TabsTrigger
                value="editor"
                title="Full code editor"
                data-testid="tab-editor"
                className="editor-tab-trigger gap-1.5 px-3 py-1.5"
              >
                <LuCode aria-hidden="true" className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Editor</span>
              </TabsTrigger>
            </TabsList>

            {(activeTab === "desktop" || activeTab === "mobile") && (
              <div className="hidden items-center gap-1.5 sm:inline-flex">
                {activeTab === "desktop" && (
                  <div className="inline-flex items-center rounded-lg bg-muted/50 p-0.5">
                    <button
                      type="button"
                      onClick={() => setDesktopViewMode("fit")}
                      title="Scale down to fit the screen"
                      aria-pressed={desktopViewMode === "fit"}
                      className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        desktopViewMode === "fit"
                          ? "bg-background text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      Fit
                      {desktopScale < 1 && (
                        <span className="ml-1 tabular-nums text-muted-foreground">
                          {Math.round(desktopScale * 100)}%
                        </span>
                      )}
                    </button>
                    <button
                      type="button"
                      onClick={() => setDesktopViewMode("actual")}
                      title="View at original size (100%)"
                      aria-pressed={desktopViewMode === "actual"}
                      className={`rounded-md px-2.5 py-1 text-[11px] font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                        desktopViewMode === "actual"
                          ? "bg-background text-foreground shadow-sm"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      100%
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {totalVersions > 0 && (
            <div className="hidden shrink-0 items-center justify-center gap-0.5 rounded-lg bg-muted/50 p-0.5 md:flex">
              <Button
                onClick={() => canGoPrev && setHead(sortedCommits[currentVersionIndex - 1].hash)}
                variant="ghost"
                size="icon"
                title="Previous version"
                aria-label="Previous version"
                className={`h-6 w-6 rounded-md text-muted-foreground hover:bg-background hover:text-foreground transition-all ${!canGoPrev ? "cursor-not-allowed opacity-30" : ""}`}
                disabled={!canGoPrev}
              >
                <LuChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
              <button
                type="button"
                onClick={onOpenVersions}
                className="flex w-36 items-center justify-center gap-1.5 rounded-md px-1 py-0.5 transition-colors hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                title={currentLabel ? `${currentLabel} — view all pages` : "View all versions"}
              >
                <span className={`min-w-0 truncate text-xs font-medium leading-none text-foreground ${currentLabel ? "font-mono text-[11px]" : ""}`}>
                  {currentLabel ?? `Version ${currentVersionIndex + 1}`}
                </span>
                {!currentLabel && currentVersionIndex === totalVersions - 1 && (
                  <span className="flex h-4 shrink-0 items-center rounded-full bg-brand/10 px-1.5 text-[10px] font-medium leading-none text-brand">
                    Latest
                  </span>
                )}
                {currentLabel && totalVersions > 1 && (
                  <span className="shrink-0 rounded-full bg-muted px-1.5 text-[10px] font-medium leading-4 tabular-nums text-muted-foreground">
                    {currentVersionIndex + 1}/{totalVersions}
                  </span>
                )}
              </button>
              <Button
                onClick={() => canGoNext && setHead(sortedCommits[currentVersionIndex + 1].hash)}
                variant="ghost"
                size="icon"
                title="Next version"
                aria-label="Next version"
                className={`h-6 w-6 rounded-md text-muted-foreground hover:bg-background hover:text-foreground transition-all ${!canGoNext ? "cursor-not-allowed opacity-30" : ""}`}
                disabled={!canGoNext}
              >
                <LuChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </div>
          )}

          <div className="flex items-center gap-1 shrink-0">
            {canSelectAndEdit && (activeTab === "desktop" || activeTab === "mobile") && (
              <SelectAndEditToolbarButton />
            )}
            {(appState === AppState.CODE_READY || isSelectedVariantComplete) && (
              <Button
                onClick={handleSaveAndRun}
                variant="ghost"
                size="sm"
                title="Save the generated site into a folder and run it locally"
                aria-label="Open folder and run the site"
                className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                disabled={isSavingProject}
                data-testid="save-run-project"
              >
                {isSavingProject ? (
                  <LuLoader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <LuFolderOpen className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span className="hidden lg:inline">Open folder &amp; run</span>
              </Button>
            )}
            {(appState === AppState.CODE_READY || isSelectedVariantComplete) && (
              <Button
                onClick={() => downloadCode(previewCode)}
                variant="ghost"
                size="icon"
                title="Download Code"
                aria-label="Download code"
                className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                data-testid="download-code"
              >
                <LuDownload aria-hidden="true" />
              </Button>
            )}
            <Button
              onClick={() => {
                const iframes = document.querySelectorAll("iframe");
                iframes.forEach((iframe) => {
                  if (iframe.srcdoc) {
                    const content = iframe.srcdoc;
                    iframe.srcdoc = "";
                    iframe.srcdoc = content;
                  }
                });
              }}
              variant="ghost"
              size="icon"
              title="Refresh Preview"
              aria-label="Refresh preview"
              className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
            >
              <LuRefreshCw aria-hidden="true" />
            </Button>
          </div>
        </div>

        <TabsContent value="desktop" className="flex-1 min-h-0 mt-0 data-[state=active]:flex data-[state=active]:flex-col">
          {showImageScanningPreview ? (
            <ImageScanningPreview imageUrl={sourceImage} />
          ) : (
            <PreviewComponent code={previewCode} device="desktop" onScaleChange={setDesktopScale} viewMode={desktopViewMode} />
          )}
        </TabsContent>
        <TabsContent value="mobile" className="flex-1 min-h-0 mt-0 data-[state=active]:flex data-[state=active]:flex-col">
          {showImageScanningPreview ? (
            <ImageScanningPreview imageUrl={sourceImage} />
          ) : (
            <PreviewComponent code={previewCode} device="mobile" viewMode="actual" />
          )}
        </TabsContent>
        <TabsContent value="code" className="flex-1 min-h-0 mt-0 overflow-auto">
          <CodeTab code={previewCode} setCode={() => {}} settings={settings} />
        </TabsContent>
        <TabsContent value="editor" className="flex-1 min-h-0 mt-0 data-[state=active]:flex data-[state=active]:flex-col overflow-hidden">
          <CodeEditorPane
            files={fileMap}
            activeFile={activeEditorFile}
            onSelectFile={setActiveEditorFile}
            settings={settings}
            onCodeChange={handleEditorCodeChange}
            onOpenPreview={() => openInNewTab(previewCode)}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default PreviewPane;
