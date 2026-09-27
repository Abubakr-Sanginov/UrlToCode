import { useMemo, useState } from "react";
import {
  LuFile,
  LuFolder,
  LuFolderOpen,
  LuChevronRight,
  LuSearch,
  LuFileCode,
  LuFileText,
} from "react-icons/lu";

interface FileNode {
  name: string;
  path: string;
  type: "file" | "folder";
  children?: FileNode[];
}

interface Props {
  files: Record<string, string>;
  activeFile: string;
  onSelectFile: (path: string) => void;
}

function buildFileTree(files: Record<string, string>): FileNode[] {
  const root: FileNode[] = [];

  for (const filePath of Object.keys(files)) {
    const parts = filePath.split("/").filter(Boolean);
    let current = root;

    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isFile = i === parts.length - 1;
      const existing = current.find((n) => n.name === part);

      if (existing) {
        if (!isFile) {
          current = existing.children!;
        }
      } else {
        const node: FileNode = {
          name: part,
          path: parts.slice(0, i + 1).join("/"),
          type: isFile ? "file" : "folder",
          children: isFile ? undefined : [],
        };
        current.push(node);
        if (!isFile) {
          current = node.children!;
        }
      }
    }
  }

  return root;
}

function getFileIcon(name: string, isActive: boolean) {
  const ext = name.split(".").pop()?.toLowerCase();
  const baseClass = `h-3.5 w-3.5 shrink-0 transition-colors`;
  switch (ext) {
    case "html":
    case "htm":
      return <LuFileCode className={`${baseClass} ${isActive ? "text-orange-400" : "text-orange-400/70"}`} />;
    case "css":
      return <LuFileCode className={`${baseClass} ${isActive ? "text-blue-400" : "text-blue-400/70"}`} />;
    case "js":
    case "jsx":
      return <LuFileCode className={`${baseClass} ${isActive ? "text-yellow-400" : "text-yellow-400/70"}`} />;
    case "ts":
    case "tsx":
      return <LuFileCode className={`${baseClass} ${isActive ? "text-blue-500" : "text-blue-500/70"}`} />;
    case "json":
      return <LuFileText className={`${baseClass} ${isActive ? "text-green-400" : "text-green-400/70"}`} />;
    case "md":
      return <LuFileText className={`${baseClass} ${isActive ? "text-gray-400" : "text-gray-400/70"}`} />;
    default:
      return <LuFile className={`${baseClass} text-muted-foreground/60`} />;
  }
}

function FileTreeNode({
  node,
  activeFile,
  onSelectFile,
  depth = 0,
}: {
  node: FileNode;
  activeFile: string;
  onSelectFile: (path: string) => void;
  depth?: number;
}) {
  const [isOpen, setIsOpen] = useState(true);
  const isActive = activeFile === node.path;

  if (node.type === "folder") {
    return (
      <div>
        <button
          type="button"
          onClick={() => setIsOpen(!isOpen)}
          className={`file-explorer-item flex w-full items-center gap-1.5 px-2 py-1 text-xs ${
            isActive ? "active" : "text-muted-foreground"
          }`}
          style={{ paddingLeft: `${depth * 14 + 8}px` }}
        >
          <LuChevronRight
            className={`h-3 w-3 shrink-0 transition-transform duration-150 ${
              isOpen ? "rotate-90 text-foreground/60" : "text-muted-foreground/60"
            }`}
          />
          {isOpen ? (
            <LuFolderOpen className="h-3.5 w-3.5 shrink-0 text-brand/70" />
          ) : (
            <LuFolder className="h-3.5 w-3.5 shrink-0 text-brand/50" />
          )}
          <span className="truncate font-medium">{node.name}</span>
        </button>
        {isOpen && node.children && (
          <div className="animate-fade-in">
            {node.children.map((child) => (
              <FileTreeNode
                key={child.path}
                node={child}
                activeFile={activeFile}
                onSelectFile={onSelectFile}
                depth={depth + 1}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onSelectFile(node.path)}
      className={`file-explorer-item flex w-full items-center gap-1.5 px-2 py-1 text-xs ${
        isActive ? "active font-medium" : "text-muted-foreground"
      }`}
      style={{ paddingLeft: `${depth * 14 + 22}px` }}
    >
      {getFileIcon(node.name, isActive)}
      <span className="truncate">{node.name}</span>
    </button>
  );
}

function FileExplorer({ files, activeFile, onSelectFile }: Props) {
  const [searchQuery, setSearchQuery] = useState("");
  const tree = useMemo(() => buildFileTree(files), [files]);

  const filteredTree = useMemo(() => {
    if (!searchQuery.trim()) return tree;

    const query = searchQuery.toLowerCase();
    function filterNode(node: FileNode): FileNode | null {
      if (node.type === "file") {
        return node.name.toLowerCase().includes(query) ? node : null;
      }
      const filteredChildren = (node.children || [])
        .map(filterNode)
        .filter(Boolean) as FileNode[];
      if (filteredChildren.length > 0) {
        return { ...node, children: filteredChildren };
      }
      return null;
    }
    return tree.map(filterNode).filter(Boolean) as FileNode[];
  }, [tree, searchQuery]);

  return (
    <div className="flex flex-col h-full">
      <div className="px-2.5 py-2 border-b border-border/60">
        <div className="flex items-center gap-1.5 rounded-lg bg-muted/50 px-2 py-1.5 transition-all focus-within:ring-1 focus-within:ring-brand/30 focus-within:bg-background">
          <LuSearch className="h-3 w-3 shrink-0 text-muted-foreground/50" />
          <input
            type="text"
            placeholder="Search..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-transparent text-xs text-foreground placeholder:text-muted-foreground/40 focus:outline-none"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-1.5 py-1">
        {filteredTree.map((node) => (
          <FileTreeNode
            key={node.path}
            node={node}
            activeFile={activeFile}
            onSelectFile={onSelectFile}
          />
        ))}
        {filteredTree.length === 0 && searchQuery && (
          <div className="flex flex-col items-center justify-center py-8 text-muted-foreground/50">
            <LuSearch className="h-6 w-6 mb-2 opacity-40" />
            <p className="text-xs">No files match</p>
            <p className="text-[10px] mt-0.5">"{searchQuery}"</p>
          </div>
        )}
      </div>
    </div>
  );
}

export default FileExplorer;
