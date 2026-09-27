import { HTTP_BACKEND_URL } from "../config";
import { Commit } from "../components/commits/types";

/**
 * "Open folder & run" pipeline.
 *
 * 1. Build a file map from the generated code (multi-page aware).
 * 2. If the browser supports the File System Access API, let the user pick a
 *    real folder and write every file into it directly.
 * 3. Mirror the same files to the backend so the site is served over HTTP and
 *    can be opened in a tab with one click.
 */

export interface ProjectFile {
  path: string;
  content: string;
}

export interface SaveRunResult {
  savedToFolder: boolean;
  folderName: string | null;
  serveUrl: string;
  fileCount: number;
}

/** Thrown when the user cancels the native folder picker. */
export class FolderPickCancelled extends Error {}

// Minimal structural types for the File System Access API. The DOM lib does
// not ship typings for the picker yet.
interface FsWritable {
  write(data: string): Promise<void>;
  close(): Promise<void>;
}
interface FsFileHandle {
  createWritable(): Promise<FsWritable>;
}
interface FsDirectoryHandle {
  readonly name: string;
  getFileHandle(name: string, options?: { create?: boolean }): Promise<FsFileHandle>;
  getDirectoryHandle(name: string, options?: { create?: boolean }): Promise<FsDirectoryHandle>;
}
type PickerWindow = Window & {
  showDirectoryPicker?: (options?: {
    mode?: "read" | "readwrite";
    id?: string;
    startIn?: string;
  }) => Promise<FsDirectoryHandle>;
};

export function supportsFolderPicker(): boolean {
  return typeof (window as PickerWindow).showDirectoryPicker === "function";
}

/** Best-effort site name used for folder naming and the README. */
export function siteNameFromCode(code: Record<string, string>): string {
  const html =
    code["project-structure"] ??
    Object.values(code).find((value) => value && value.trim()) ??
    "";
  const match = html.match(/<title[^>]*>([^<]{1,80})<\/title>/i);
  return match ? match[1].trim() : "cloned-site";
}

export function slugifyPagePath(raw: string): string {
  const cleaned = raw
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return cleaned || "page";
}

/**
 * Maps the url-to-code output (`{ "project-structure": html, "/about": html, ... }`)
 * onto a flat file tree rooted at `index.html`.
 */
export function buildProjectFiles(
  code: Record<string, string>,
  siteName?: string
): ProjectFile[] {
  const keys = Object.keys(code).filter((key) => code[key] && code[key].trim());
  if (keys.length === 0) return [];

  const files: ProjectFile[] = [];
  const usedNames = new Set<string>();
  const portalKey = keys.includes("project-structure") ? "project-structure" : null;

  const uniqueName = (base: string) => {
    let name = base;
    let counter = 2;
    while (usedNames.has(name)) {
      name = base.replace(/(\.[a-z0-9]+)?$/i, (ext) => `-${counter}${ext ?? ""}`);
      counter += 1;
    }
    usedNames.add(name);
    return name;
  };

  // The crawled root page is the clone's real home page, so it owns
  // index.html. Generated pages link to each other by these names
  // (backend/prompts/url_to_code_prompts.py `page_filename`).
  const rootKey = keys.includes("/") ? "/" : null;
  if (rootKey) {
    files.push({ path: uniqueName("index.html"), content: code[rootKey] });
  }

  for (const key of keys) {
    if (key === portalKey || key === rootKey) continue;
    if (key === "database-schema") {
      files.push({ path: "database-schema.sql", content: code[key] });
      continue;
    }
    files.push({
      path: uniqueName(`${slugifyPagePath(key)}.html`),
      content: code[key],
    });
  }

  // The portal is a generated overview, not part of the copy: it only takes
  // index.html when the crawl has no root page.
  if (portalKey) {
    const portalName =
      rootKey || usedNames.has("index.html") ? uniqueName("portal.html") : uniqueName("index.html");
    files.push({ path: portalName, content: code[portalKey] });
  }

  files.push({
    path: "README.md",
    content: buildReadme(siteName ?? "Generated site", files.map((f) => f.path)),
  });
  return files;
}

/**
 * Rebuilds the generated-code map from project commits. Cloned pages are
 * stored as `code_create` commits whose label is the page path ("Portal" for
 * the landing page).
 */
export function buildCodeMapFromCommits(
  commits: Record<string, Commit>
): Record<string, string> {
  const code: Record<string, string> = {};
  for (const commit of Object.values(commits)) {
    if (commit.type !== "code_create") continue;
    const variantCode = commit.variants[commit.selectedVariantIndex]?.code;
    if (!variantCode || !variantCode.trim()) continue;
    const key = commit.label === "Portal" ? "project-structure" : commit.label ?? "/";
    code[key] = variantCode;
  }
  return code;
}

function buildReadme(siteName: string, fileNames: string[]): string {
  const list = fileNames.map((name) => `- \`${name}\``).join("\n");
  return `# ${siteName}

Generated by UrlToCode.

## Run locally

Open \`index.html\` in a browser, or serve this folder with any static server:

\`\`\`bash
python -m http.server 8080
\`\`\`

## Files

${list}
`;
}

async function writeFileToDirectory(
  dir: FsDirectoryHandle,
  file: ProjectFile
): Promise<void> {
  const segments = file.path.split("/");
  const fileName = segments.pop();
  if (!fileName) throw new Error(`Invalid file path: ${file.path}`);

  let current = dir;
  for (const segment of segments) {
    current = await current.getDirectoryHandle(segment, { create: true });
  }
  const handle = await current.getFileHandle(fileName, { create: true });
  const writable = await handle.createWritable();
  try {
    await writable.write(file.content);
  } finally {
    await writable.close();
  }
}

interface BackendSaveResponse {
  runId: string;
  files: number;
  url: string;
}

async function mirrorToBackend(
  files: ProjectFile[],
  siteName: string
): Promise<string> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/local-project/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      siteName,
      files: files.map((file) => ({ path: file.path, content: file.content })),
    }),
  });
  if (!response.ok) {
    let detail = `status ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status-code detail
    }
    throw new Error(`Backend could not save the project (${detail})`);
  }
  const data = (await response.json()) as BackendSaveResponse;
  return `${HTTP_BACKEND_URL}${data.url}`;
}

/**
 * Writes the project into a user-picked folder (when supported) and starts a
 * local server for it. Returns where the site can be opened.
 */
export async function saveAndRunProject(
  code: Record<string, string>,
  siteName: string
): Promise<SaveRunResult> {
  const files = buildProjectFiles(code, siteName);
  if (files.length === 0) {
    throw new Error("Nothing to save yet — wait for the clone to finish.");
  }

  let folderName: string | null = null;
  if (supportsFolderPicker()) {
    const picker = (window as PickerWindow).showDirectoryPicker;
    try {
      // `id` reuses the last location and permissions for this picker.
      const dir = await picker!.call(window, { mode: "readwrite", id: "urltocode-sites" });
      folderName = dir.name;
      for (const file of files) {
        await writeFileToDirectory(dir, file);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new FolderPickCancelled("Folder selection cancelled");
      }
      console.warn("Folder write failed, falling back to backend only", error);
      folderName = null;
    }
  }

  const serveUrl = await mirrorToBackend(files, siteName);
  return {
    savedToFolder: folderName !== null,
    folderName,
    serveUrl,
    fileCount: files.length,
  };
}
