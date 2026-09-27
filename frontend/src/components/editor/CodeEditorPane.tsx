import { useState, useCallback, useMemo } from "react";
import {
  LuX,
  LuFileCode,
  LuDownload,
  LuExternalLink,
  LuPanelLeftClose,
  LuPanelLeftOpen,
  LuTerminal,
  LuCode,
  LuFolder,
} from "react-icons/lu";
import { FaCopy } from "react-icons/fa";
import toast from "react-hot-toast";
import copy from "copy-to-clipboard";
import CodeMirror from "../preview/CodeMirror";
import FileExplorer from "./FileExplorer";
import { Settings } from "../../types";
import { Button } from "../ui/button";
import { downloadCode } from "../preview/download";

interface FileTab {
  path: string;
  label: string;
}

interface Props {
  files: Record<string, string>;
  activeFile: string;
  onSelectFile: (path: string) => void;
  settings: Settings;
  onCodeChange?: (path: string, code: string) => void;
  onOpenPreview?: () => void;
}

function getLanguageFromPath(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "html":
    case "htm":
      return "HTML";
    case "css":
      return "CSS";
    case "js":
    case "jsx":
      return "JavaScript";
    case "ts":
    case "tsx":
      return "TypeScript";
    case "json":
      return "JSON";
    case "md":
      return "Markdown";
    default:
      return "Text";
  }
}

function getFileIconColor(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "html":
    case "htm":
      return "text-orange-400";
    case "css":
      return "text-blue-400";
    case "js":
    case "jsx":
      return "text-yellow-400";
    case "ts":
    case "tsx":
      return "text-blue-500";
    case "json":
      return "text-green-400";
    case "md":
      return "text-gray-400";
    default:
      return "text-muted-foreground";
  }
}

function CodeEditorPane({
  files,
  activeFile,
  onSelectFile,
  settings,
  onCodeChange,
  onOpenPreview,
}: Props) {
  const [isExplorerOpen, setIsExplorerOpen] = useState(true);
  const [openTabs, setOpenTabs] = useState<FileTab[]>(() => {
    const initialPath = Object.keys(files)[0] || "";
    return initialPath
      ? [{ path: initialPath, label: initialPath.split("/").pop() || initialPath }]
      : [];
  });

  const fileEntries = useMemo(() => Object.keys(files), [files]);

  const activeCode = files[activeFile] || "";
  const activeLanguage = getLanguageFromPath(activeFile);

  const openFile = useCallback(
    (path: string) => {
      onSelectFile(path);
      setOpenTabs((prev) => {
        const exists = prev.some((t) => t.path === path);
        if (exists) return prev;
        const label = path.split("/").pop() || path;
        return [...prev, { path, label }];
      });
    },
    [onSelectFile]
  );

  const closeTab = useCallback(
    (path: string, e?: React.SyntheticEvent) => {
      e?.stopPropagation();
      setOpenTabs((prev) => {
        const filtered = prev.filter((t) => t.path !== path);
        if (filtered.length === 0 && fileEntries.length > 0) {
          const newPath = fileEntries[0];
          onSelectFile(newPath);
          return [{ path: newPath, label: newPath.split("/").pop() || newPath }];
        }
        if (prev.findIndex((t) => t.path === path) < prev.findIndex((t) => t.path === activeFile)) {
          const currentIdx = prev.findIndex((t) => t.path === activeFile);
          if (currentIdx > 0) {
            onSelectFile(prev[currentIdx - 1].path);
          }
        } else if (path === activeFile && filtered.length > 0) {
          onSelectFile(filtered[filtered.length - 1].path);
        }
        return filtered;
      });
    },
    [activeFile, fileEntries, onSelectFile]
  );

  const copyCode = useCallback(() => {
    copy(activeCode);
    toast.success("Copied to clipboard");
  }, [activeCode]);

  const handleCodeChange = useCallback(
    (newCode: string) => {
      if (onCodeChange) {
        onCodeChange(activeFile, newCode);
      }
    },
    [activeFile, onCodeChange]
  );

  return (
    <div className="flex flex-col h-full bg-background">
      {/* Editor toolbar */}
      <div className="editor-toolbar flex items-center justify-between px-2 py-1.5 shrink-0">
        <div className="flex items-center gap-1">
          <Button
            onClick={() => setIsExplorerOpen(!isExplorerOpen)}
            variant="ghost"
            size="icon"
            title={isExplorerOpen ? "Close file explorer" : "Open file explorer"}
            className="h-7 w-7 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
          >
            {isExplorerOpen ? (
              <LuPanelLeftClose className="h-3.5 w-3.5" />
            ) : (
              <LuPanelLeftOpen className="h-3.5 w-3.5" />
            )}
          </Button>
          <div className="h-4 w-px bg-border/60 mx-1" />
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground px-1">
            <LuFolder className="h-3 w-3 text-brand" />
            <span className="font-medium">{fileEntries.length}</span>
            <span>file{fileEntries.length !== 1 ? "s" : ""}</span>
          </div>
        </div>

        <div className="flex items-center gap-0.5">
          <Button
            onClick={copyCode}
            variant="ghost"
            size="sm"
            title="Copy current file"
            className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
          >
            <FaCopy className="h-3 w-3" />
            <span className="hidden sm:inline">Copy</span>
          </Button>
          <Button
            onClick={() => downloadCode(activeCode)}
            variant="ghost"
            size="sm"
            title="Download current file"
            className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
          >
            <LuDownload className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Download</span>
          </Button>
          {onOpenPreview && (
            <Button
              onClick={onOpenPreview}
              variant="ghost"
              size="sm"
              title="Preview in browser"
              className="h-7 gap-1.5 px-2 text-xs text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-all"
            >
              <LuExternalLink className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Preview</span>
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* File explorer sidebar */}
        {isExplorerOpen && (
          <div className="w-56 shrink-0 border-r border-border/60 bg-card/50 overflow-hidden flex flex-col animate-fade-in">
            <div className="px-3 py-2.5 border-b border-border/60">
              <h3 className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/80">
                Explorer
              </h3>
            </div>
            <FileExplorer files={files} activeFile={activeFile} onSelectFile={openFile} />
          </div>
        )}

        {/* Editor area */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {/* File tabs */}
          {openTabs.length > 0 && (
            <div className="flex items-center border-b border-border/60 bg-card/30 overflow-x-auto shrink-0">
              {openTabs.map((tab) => {
                const isActive = tab.path === activeFile;
                const iconColor = getFileIconColor(tab.path);
                return (
                  <button
                    key={tab.path}
                    type="button"
                    onClick={() => onSelectFile(tab.path)}
                    className={`file-tab group flex items-center gap-1.5 px-3 py-2 text-xs shrink-0 ${
                      isActive
                        ? "active bg-background text-foreground"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                  >
                    <LuFileCode className={`h-3.5 w-3.5 shrink-0 ${isActive ? iconColor : ""}`} />
                    <span className="truncate max-w-[120px]">{tab.label}</span>
                    <span
                      role="button"
                      tabIndex={0}
                      onClick={(e) => closeTab(tab.path, e)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") closeTab(tab.path);
                      }}
                      className="ml-1 rounded p-0.5 opacity-0 group-hover:opacity-100 hover:bg-muted/80 transition-all"
                      title="Close tab"
                    >
                      <LuX className="h-3 w-3" />
                    </span>
                  </button>
                );
              })}
            </div>
          )}

          {/* Breadcrumb / file info */}
          <div className="editor-statusbar flex items-center gap-2 px-3 py-1.5 shrink-0">
            <LuFileCode className={`h-3 w-3 ${getFileIconColor(activeFile)}`} />
            <span className="font-mono text-muted-foreground">{activeFile || "No file selected"}</span>
            {activeFile && (
              <>
                <span className="text-muted-foreground/30">|</span>
                <span className="text-brand/80 font-medium">{activeLanguage}</span>
                <span className="text-muted-foreground/30">|</span>
                <span>{activeCode.split("\n").length} lines</span>
                <span className="text-muted-foreground/30">|</span>
                <span>{(new TextEncoder().encode(activeCode).length / 1024).toFixed(1)} KB</span>
              </>
            )}
          </div>

          {/* Code editor */}
          <div className="flex-1 min-h-0 overflow-hidden">
            {activeFile ? (
              <CodeMirror code={activeCode} editorTheme={settings.editorTheme} onCodeChange={handleCodeChange} />
            ) : (
              <div className="flex flex-col items-center justify-center h-full text-muted-foreground">
                <div className="w-16 h-16 rounded-2xl bg-muted/50 flex items-center justify-center mb-4">
                  <LuCode className="h-8 w-8 opacity-30" />
                </div>
                <p className="text-sm font-medium">Select a file to edit</p>
                <p className="text-xs text-muted-foreground/60 mt-1">Choose from the explorer on the left</p>
              </div>
            )}
          </div>

          {/* Status bar */}
          <div className="editor-statusbar flex items-center justify-between border-t border-border/60 px-3 py-1.5 shrink-0">
            <div className="flex items-center gap-4">
              <span className="flex items-center gap-1.5 text-brand/80">
                <LuTerminal className="h-3 w-3" />
                <span className="font-medium">Code Editor</span>
              </span>
              <span className="text-muted-foreground/60">UTF-8</span>
              <span className="text-muted-foreground/60">Spaces: 2</span>
            </div>
            <div className="flex items-center gap-4">
              <span className="tabular-nums">Ln {activeCode.split("\n").length}</span>
              <span className="tabular-nums">Col 1</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default CodeEditorPane;
