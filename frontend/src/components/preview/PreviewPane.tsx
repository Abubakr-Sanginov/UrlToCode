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
  LuImage,
  LuColumns,
  LuPackage,
  LuGauge,
  LuWrench,
  LuPlay,
  LuLibrary,
  LuShare2,
} from "react-icons/lu";
import { useMemo, useState, useCallback } from "react";
import toast from "react-hot-toast";
import { AppState, Settings } from "../../types";
import CodeTab from "./CodeTab";
import CodeEditorPane from "../editor/CodeEditorPane";
import { ShareDialog } from "./ShareDialog";
import { Button } from "../ui/button";
import { useAppStore } from "../../store/app-store";
import { useProjectStore } from "../../store/project-store";
import DiffOverlay from "./DiffOverlay";
import { useCloneFidelity, fidelityFor } from "../../hooks/useCloneFidelity";
import { useCloneResume } from "../../hooks/useCloneResume";
import { fidelityLabel, fidelityVerdict } from "../../lib/cloneRuns";
import { extractHtml } from "./extractHtml";
import PreviewComponent from "./PreviewComponent";
import { downloadCode } from "./download";
import { SelectAndEditToolbarButton } from "../select-and-edit/SelectAndEditControls";
import { normalizeBabelCdn } from "../../lib/babelCdn";
import ImageScanningPreview from "./ImageScanningPreview";
import {
  FolderPickCancelled,
  buildProjectFiles,
  GENERATED_FILE_PREFIX,
  downloadProjectZip,
  saveAndRunProject,
  siteNameFromCode,
} from "../../lib/localProject";
import { pagePathForLabel, useCloneStore } from "../../store/clone-store";
import {
  frameworkPageFileMap,
  parseFrameworkPage,
  rebuildPreviewSource,
} from "../../lib/frameworkProject";
import { buildCodeMapFromCommits, projectFileForPage } from "../../lib/projectFiles";
import { mediaUrl } from "../../config";
import OriginalScreenshot from "./OriginalScreenshot";
import { CloneLibrary } from "./CloneLibrary";
import { pushDevFile } from "../../lib/localProject";

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
  /**
   * Generate the page on screen again from the run's stored crawl. Only
   * offered for clone pages, which is why the pane decides whether to show it
   * rather than the button itself.
   */
  onRegeneratePage: () => void;
}

function PreviewPane({ settings, onOpenVersions, onRegeneratePage }: Props) {
  const { appState } = useAppStore();
  const { inputMode, head, commits, setHead, fileOverrides, setFileOverride } =
    useProjectStore();
  // The site the clone was made from, so the library can say what a saved
  // project is a clone of. Empty for anything hand-written.
  const cloneSourceUrl = useCloneStore((s) => s.baseUrl ?? "");
  // Whether a dev server is running for this run, so an edit can be pushed
  // to disk and hot-reloaded instead of only living in the editor.
  const devServerFor = useCloneStore((s) => Boolean(s.devServerRunId));
  const [activeTab, setActiveTab] = useState("desktop");
  const [desktopScale, setDesktopScale] = useState(1);
  const [desktopViewMode, setDesktopViewMode] = useState<"fit" | "actual">("fit");
  const [isSavingProject, setIsSavingProject] = useState(false);
  const [isZipping, setIsZipping] = useState(false);
  // The saved clones on this backend. Off until asked for: it is a list of
  // past work, not part of the work in front of the user.
  const [showLibrary, setShowLibrary] = useState(false);
  const [isShareOpen, setIsShareOpen] = useState(false);
  const { screenshots, sourceUrls, runId } = useCloneStore();
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

  // The original page this version clones, when the run captured it.
  const pagePath = pagePathForLabel(currentLabel);
  const originalShot = pagePath ? screenshots[pagePath] : undefined;
  const originalUrl = pagePath ? sourceUrls[pagePath] : undefined;
  // Switching to a page without a screenshot falls back to the clone.
  const shownTab =
    (activeTab === "original" || activeTab === "compare") && !originalShot
      ? "desktop"
      : activeTab;

  const isSelectedVariantComplete =
    !!selectedVariant && selectedVariant.status === "complete";

  // How close the clone is to the original, per page. The repairs it produces
  // are new code for the project's files, so they arrive the same way a hand
  // edit does.
  const { state: fidelity, check, repair } = useCloneFidelity(
    runId,
    settings,
    useCallback((code: Record<string, string>) => {
      const codeMap = buildCodeMapFromCommits(commits);
      for (const [key, generated] of Object.entries(code)) {
        if (key === "project-structure" || key === "database-schema") continue;
        const filePath = projectFileForPage(codeMap, key);
        if (filePath) setFileOverride(filePath, generated);
      }
    }, [commits, setFileOverride])
  );

  // A run that stopped part way through. Generation belongs to the run rather
  // than to the connection now, so coming back is a question - how much is
  // left, and would you like to finish it - rather than a restart.
  const { state: resume, canResume, resume: resumeRun, stop: stopRun } = useCloneResume(
    runId,
    settings,
    useCallback(
      (incoming: Record<string, string>) => {
        // The run sends its whole code map, so the pages that were already
        // here come back too. Those are left alone: an override is the
        // user's own edit, and a resumed run must not quietly replace it.
        // A page that had nothing before is new, and a first override is
        // how it reaches the project.
        const known = buildCodeMapFromCommits(commits);
        for (const [key, generated] of Object.entries(incoming)) {
          if (!generated.trim()) continue;
          const filePath = projectFileForPage(
            { ...known, [key]: generated },
            key
          );
          if (filePath && !(filePath in fileOverrides)) {
            setFileOverride(filePath, generated);
          }
        }
      },
      [commits, fileOverrides, setFileOverride]
    )
  );

  const pageFidelity = useMemo(
    () => fidelityFor(fidelity.result, pagePath),
    [fidelity.result, pagePath]
  );
  const pageScore = pageFidelity && !pageFidelity.error ? pageFidelity.score : null;
  const renderShot =
    pageFidelity?.renderFile && runId ? mediaUrl(pageFidelity.renderFile) : undefined;

  // Which project file holds each page, so an editor change to a page
  // component can be pushed back into the document the preview renders.
  const pageFileMap = useMemo(
    () => frameworkPageFileMap(buildCodeMapFromCommits(commits)),
    [commits]
  );

  const previewCode = useMemo(() => {
    const base =
      inputMode === "video" && appState === AppState.CODING
        ? extractHtml(currentCode)
        : currentCode;
    // The portal is a generated page like any other, but it is labelled
    // "Portal" rather than by path, so it has no project file to edit.
    if (!pagePath || !pageFileMap || !base) return base;
    const edited = fileOverrides[pageFileMap.get(pagePath) ?? ""];
    return edited === undefined ? base : rebuildPreviewSource(base, edited);
  }, [currentCode, inputMode, appState, pagePath, pageFileMap, fileOverrides]);

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

  const framework = parseFrameworkPage(previewCode)?.framework ?? null;
  // Source of the page on screen, not the preview wrapper around it.
  const quickViewCode = parseFrameworkPage(previewCode)?.source ?? previewCode;

  const generatedFiles = useCloneStore((state) => state.generatedFiles);

  // The editor and the saved project must agree on file names, so both read
  // the same layout function rather than deriving names separately.
  const fileMap = useMemo(() => {
    const code = buildCodeMapFromCommits(commits);
    const source =
      Object.keys(code).length > 0 ? code : previewCode ? { "/": previewCode } : {};
    // The generated server and mock layer join the pages here, so they reach
    // the editor, the ZIP and the saved folder without ever becoming a
    // version.
    const withExtras = {
      ...source,
      ...Object.fromEntries(
        Object.entries(generatedFiles).map(([path, content]) => [
          `${GENERATED_FILE_PREFIX}${path}`,
          content,
        ])
      ),
    };
    const files = buildProjectFiles(withExtras, siteNameFromCode(withExtras), fileOverrides);
    if (files.length === 0) return {};
    return Object.fromEntries(
      files
        .filter((file) => !file.path.startsWith("preview/"))
        .map((file) => [file.path, file.content])
    );
  }, [commits, previewCode, fileOverrides, generatedFiles]);

  const editorFile =
    activeEditorFile in fileMap ? activeEditorFile : Object.keys(fileMap)[0] ?? "";

  const handleEditorCodeChange = useCallback(
    (path: string, newCode: string) => {
      setFileOverride(path, newCode);
      // A running dev server reloads itself when the file changes on disk.
      // Pushing is best effort: the edit is already saved, so a dev server
      // that is not running must not stop the user editing.
      const runId = useCloneStore.getState().runId;
      if (runId && devServerFor) {
        void pushDevFile(runId, path, newCode).catch((error) => {
          console.warn("Could not push the edit to the dev server", error);
        });
      }
      toast.success(`Saved changes to ${path} (${newCode.length} chars)`);
    },
    [setFileOverride, devServerFor]
  );

  /** Every generated page, or null (with a toast) while there is none. */
  const collectProjectCode = (): Record<string, string> | null => {
    const committedCode = buildCodeMapFromCommits(commits);
    const projectCode =
      Object.keys(committedCode).length > 0 ? committedCode : { "/": previewCode };
    const hasContent = Object.values(projectCode).some(
      (value) => value && value.trim()
    );
    if (!hasContent) {
      toast.error("Wait for the code to finish generating first.");
      return null;
    }
    return projectCode;
  };

  const handleDownloadZip = async () => {
    if (isZipping) return;
    const projectCode = collectProjectCode();
    if (!projectCode) return;

    setIsZipping(true);
    try {
      const fileCount = await downloadProjectZip(
        projectCode,
        siteNameFromCode(projectCode),
        fileOverrides
      );
      toast.success(`ZIP with ${fileCount} file${fileCount === 1 ? "" : "s"} is downloading`);
    } catch (error) {
      console.error("ZIP download failed", error);
      toast.error(error instanceof Error ? error.message : "Could not build the ZIP.");
    } finally {
      setIsZipping(false);
    }
  };

  const handleSaveAndRun = async () => {
    if (isSavingProject) return;

    const projectCode = collectProjectCode();
    if (!projectCode) return;

    setIsSavingProject(true);
    try {
      const result = await saveAndRunProject(
        projectCode,
        siteNameFromCode(projectCode),
        fileOverrides,
        cloneSourceUrl
      );
      if (result.savedToFolder) {
        toast.success(
          `Saved ${result.fileCount} file${result.fileCount === 1 ? "" : "s"} to "${result.folderName}"` +
            (result.isFrameworkProject
              ? " - run `npm install && npm run dev` there"
              : "")
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
      {showLibrary && <CloneLibrary onClose={() => setShowLibrary(false)} />}
      <Tabs
        value={shownTab}
        onValueChange={setActiveTab}
        className="flex-1 flex flex-col min-h-0"
      >
{/* Toolbar. Both halves refuse to shrink (see below), so this row scrolls
            sideways rather than letting its labels collide when the preview
            pane is dragged narrow. Overlapping text is unreadable; a
            scrollbar is not. */}
        <div className="editor-toolbar relative flex shrink-0 items-center justify-between gap-2 overflow-x-auto px-3 py-2">
          <div className="flex min-w-0 shrink-0 items-center gap-3">
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
              {originalShot && (
                <TabsTrigger
                  value="original"
                  title="Screenshot of the original page"
                  data-testid="tab-original"
                  className="editor-tab-trigger gap-1.5 px-3 py-1.5"
                >
                  <LuImage aria-hidden="true" className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Original</span>
                </TabsTrigger>
              )}
              {originalShot && (
                <TabsTrigger
                  value="compare"
                  title="Clone and original side by side"
                  data-testid="tab-compare"
                  className="editor-tab-trigger gap-1.5 px-3 py-1.5"
                >
                  <LuColumns aria-hidden="true" className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Compare</span>
                </TabsTrigger>
              )}
            </TabsList>

            {(shownTab === "desktop" || shownTab === "mobile") && (
              <div className="hidden shrink-0 items-center gap-1.5 sm:inline-flex">
                {shownTab === "desktop" && (
                  // shrink-0: a toolbar crowded enough to squeeze this
                  // control would overlap "Fit 80%" and "100%" on top of
                  // each other, because the labels do not wrap to hide
                  // the collision.
                  <div className="inline-flex shrink-0 items-center whitespace-nowrap rounded-lg bg-muted/50 p-0.5">
                    <button
                      type="button"
                      onClick={() => setDesktopViewMode("fit")}
                      title="Scale down to fit the screen"
                      aria-pressed={desktopViewMode === "fit"}
                      className={`whitespace-nowrap rounded-md px-2.5 py-1 text-[11px] font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
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
                      className={`whitespace-nowrap rounded-md px-2.5 py-1 text-[11px] font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
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
                {/* The score belongs to the page, so it sits next to the page
                    name rather than in a list the user has to open. */}
                {pageScore !== null && (
                  <span
                    data-testid="page-fidelity"
                    className={`shrink-0 text-[10px] font-medium leading-4 tabular-nums ${
                      pageScore >= 0.95
                        ? "text-emerald-500"
                        : pageScore >= 0.8
                          ? "text-amber-500"
                          : "text-red-500"
                    }`}
                    title={fidelityLabel(fidelityVerdict(pageScore))}
                  >
                    {Math.round(pageScore * 100)}%
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
            {canSelectAndEdit && (shownTab === "desktop" || shownTab === "mobile") && (
              <SelectAndEditToolbarButton />
            )}
            {/* Only a page the crawl actually visited can be generated again;
                the portal is generated with the run and has no crawl of its
                own, so offering a retry there would only fail. */}
            {runId && pagePath && originalShot && (
              <Button
                onClick={onRegeneratePage}
                variant="ghost"
                size="sm"
                title="Generate this page again from the original site"
                aria-label="Regenerate this page"
                className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                data-testid="regenerate-page"
              >
                <LuRefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                <span className="hidden lg:inline">Regenerate page</span>
              </Button>
            )}
            {/* Checking renders this page in the backend's browser and compares
                it with the screenshot taken while crawling, so it is offered
                for a page the crawl actually visited. */}
            {runId && pagePath && originalShot && (
              <Button
                onClick={() => void check([pagePath])}
                variant="ghost"
                size="sm"
                disabled={fidelity.isChecking || fidelity.isRepairing}
                title="Compare this page with the original, pixel for pixel"
                aria-label="Check how close this page is to the original"
                className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                data-testid="check-fidelity"
              >
                {fidelity.isChecking ? (
                  <LuLoader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <LuGauge className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span className="hidden lg:inline">Check</span>
              </Button>
            )}
            {/* A run that stopped early is still on the server with the pages
                it finished. Continuing costs a model call per missing page, so
                it is a button rather than something that happens on its own. */}
            {runId && resume.status?.known && (
              resume.status.running ? (
                <Button
                  onClick={() => void stopRun()}
                  variant="ghost"
                  size="sm"
                  disabled={resume.isWorking}
                  title="Stop this run. Every page it has finished is kept."
                  aria-label="Stop the running clone"
                  className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                  data-testid="stop-run"
                >
                  <LuLoader2
                    className="h-3.5 w-3.5 animate-spin"
                    aria-hidden="true"
                  />
                  <span className="hidden lg:inline">
                    {resume.status.pagesDone ?? 0} page(s) done
                  </span>
                </Button>
              ) : (
                canResume && (
                  <Button
                    onClick={() => void resumeRun()}
                    variant="ghost"
                    size="sm"
                    disabled={resume.isWorking}
                    title={`Generate the ${
                      resume.status.pagesPending ?? 0
                    } page(s) this run never finished`}
                    aria-label="Continue generating the remaining pages"
                    className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                    data-testid="resume-run"
                  >
                    {resume.isWorking ? (
                      <LuLoader2
                        className="h-3.5 w-3.5 animate-spin"
                        aria-hidden="true"
                      />
                    ) : (
                      <LuPlay className="h-3.5 w-3.5" aria-hidden="true" />
                    )}
                    <span className="hidden lg:inline">
                      Continue ({resume.status.pagesPending})
                    </span>
                  </Button>
                )
              )
            )}
            {/* A repair is a model call, so it is only offered once there is a
                score to act on and the page is actually below the bar. */}
            {runId && pagePath && pageScore !== null && (
              <Button
                onClick={() => void repair([pagePath])}
                variant="ghost"
                size="sm"
                disabled={fidelity.isRepairing || fidelity.isChecking}
                title="Generate this page again, told exactly where it differs from the original"
                aria-label="Fix this page"
                className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                data-testid="fix-page"
              >
                {fidelity.isRepairing ? (
                  <LuLoader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <LuWrench className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span className="hidden lg:inline">
                  {fidelity.isRepairing ? "Fixing..." : "Fix this page"}
                </span>
              </Button>
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
                onClick={handleDownloadZip}
                variant="ghost"
                size="sm"
                title="Download the whole project (pages, images, videos) as a ZIP"
                aria-label="Download project as ZIP"
                className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                disabled={isZipping}
                data-testid="download-zip"
              >
                {isZipping ? (
                  <LuLoader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
                ) : (
                  <LuPackage className="h-3.5 w-3.5" aria-hidden="true" />
                )}
                <span className="hidden lg:inline">ZIP</span>
              </Button>
            )}
            {(appState === AppState.CODE_READY || isSelectedVariantComplete) && (
              <Button
                onClick={() => setShowLibrary((open) => !open)}
                variant="ghost"
                size="sm"
                title="Every clone this backend has saved"
                aria-label="Clone library"
                className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                data-testid="open-library"
              >
                <LuLibrary className="h-3.5 w-3.5" aria-hidden="true" />
                <span className="hidden lg:inline">Library</span>
              </Button>
            )}
            {(appState === AppState.CODE_READY || isSelectedVariantComplete) && (
              <>
                <Button
                  onClick={() => setIsShareOpen(true)}
                  variant="ghost"
                  size="icon"
                  title="Share this site"
                  aria-label="Share this site"
                  className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                  data-testid="share-open"
                >
                  <LuShare2 aria-hidden="true" />
                </Button>
                <Button
                  onClick={() => (framework ? handleDownloadZip() : downloadCode(previewCode))}
                  variant="ghost"
                  size="icon"
                  title="Download Code"
                  aria-label="Download code"
                  className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
                  data-testid="download-code"
                >
                  <LuDownload aria-hidden="true" />
                </Button>
              </>
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
        {originalShot && (
          <TabsContent value="original" className="flex-1 min-h-0 mt-0 data-[state=active]:flex data-[state=active]:flex-col">
            <OriginalScreenshot src={originalShot} url={originalUrl} />
          </TabsContent>
        )}
        {originalShot && (
          <TabsContent value="compare" className="flex-1 min-h-0 mt-0 data-[state=active]:flex">
            {/* Once a check has rendered the page, the two pictures can be
                wiped against each other with the differences marked, which
                says far more than two screenshots sat next to each other.
                Before that there is no render to compare, so the plain
                side-by-side is all there is to show. */}
            {renderShot && pageFidelity ? (
              <DiffOverlay
                originalUrl={originalShot}
                cloneUrl={renderShot}
                score={pageScore}
                regions={pageFidelity.regions}
                originalWidth={pageFidelity.originalWidth}
                originalHeight={pageFidelity.originalHeight}
                renderedHeight={pageFidelity.renderedHeight}
                viewports={pageFidelity.viewports}
              />
            ) : (
              <>
                <div className="flex min-w-0 flex-1 flex-col border-r border-border">
                  <div className="shrink-0 border-b border-border px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Clone
                  </div>
                  <PreviewComponent code={previewCode} device="desktop" viewMode="fit" />
                </div>
                <div className="flex min-w-0 flex-1 flex-col">
                  <OriginalScreenshot src={originalShot} url={originalUrl} />
                </div>
              </>
            )}
          </TabsContent>
        )}
        <TabsContent value="code" className="flex-1 min-h-0 mt-0 overflow-auto">
          <CodeTab code={quickViewCode} setCode={() => {}} settings={settings} />
        </TabsContent>
        <TabsContent value="editor" className="flex-1 min-h-0 mt-0 data-[state=active]:flex data-[state=active]:flex-col overflow-hidden">
          <CodeEditorPane
            files={fileMap}
            activeFile={editorFile}
            onSelectFile={setActiveEditorFile}
            settings={settings}
            onCodeChange={handleEditorCodeChange}
            onOpenPreview={() => openInNewTab(previewCode)}
          />
        </TabsContent>
      </Tabs>
      {isShareOpen && runId && (
        <ShareDialog
          runId={runId}
          onClose={() => setIsShareOpen(false)}
        />
      )}
    </div>
  );
}

export default PreviewPane;
