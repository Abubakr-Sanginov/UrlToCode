import { HTTP_BACKEND_URL } from "../config";
import { ProjectFile, buildProjectFiles } from "./projectFiles";

// The file layout lives in projectFiles.ts; it is re-exported here because
// every caller of the save/zip pipeline already imports this module.
export {
  buildCodeMapFromCommits,
  buildProjectFiles,
  generatedPathFor,
  GENERATED_FILE_PREFIX,
  siteNameFromCode,
  slugifyPagePath,
} from "./projectFiles";
export type { ProjectFile } from "./projectFiles";

/**
 * "Open folder & run" pipeline.
 *
 * 1. Build a file map from the generated code (multi-page aware).
 * 2. If the browser supports the File System Access API, let the user pick a
 *    real folder and write every file into it directly.
 * 3. Mirror the same files to the backend so the site is served over HTTP and
 *    can be opened in a tab with one click.
 */

export interface SaveRunResult {
  savedToFolder: boolean;
  /** True when the files form a Next.js / React project (run with npm). */
  isFrameworkProject: boolean;
  folderName: string | null;
  serveUrl: string;
  fileCount: number;
}

/** Thrown when the user cancels the native folder picker. */
export class FolderPickCancelled extends Error {}

// Minimal structural types for the File System Access API. The DOM lib does
// not ship typings for the picker yet.
interface FsWritable {
  write(data: string | Blob): Promise<void>;
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

async function writeFileToDirectory(
  dir: FsDirectoryHandle,
  file: { path: string; content: string | Blob }
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
  /** Captured images/videos the backend copied into the project. */
  assets?: { path: string; url: string }[];
  /** Files whose media URLs now point at those copies. */
  rewritten?: ProjectFile[];
}

async function mirrorToBackend(
  files: ProjectFile[],
  siteName: string,
  sourceUrl: string = ""
): Promise<BackendSaveResponse> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/local-project/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      siteName,
      sourceUrl,
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
  return (await response.json()) as BackendSaveResponse;
}

/** Writes the project, with the backend's media copies, into `dir`. */
async function writeProjectToDirectory(
  dir: FsDirectoryHandle,
  files: ProjectFile[],
  saved: BackendSaveResponse
): Promise<number> {
  const rewritten = new Map((saved.rewritten ?? []).map((f) => [f.path, f.content]));
  for (const file of files) {
    await writeFileToDirectory(dir, {
      path: file.path,
      content: rewritten.get(file.path) ?? file.content,
    });
  }

  let written = files.length;
  for (const asset of saved.assets ?? []) {
    try {
      const response = await fetch(`${HTTP_BACKEND_URL}${asset.url}`);
      if (!response.ok) throw new Error(`status ${response.status}`);
      await writeFileToDirectory(dir, { path: asset.path, content: await response.blob() });
      written += 1;
    } catch (error) {
      // One missing picture must not cost the rest of the project.
      console.warn(`Could not save ${asset.path}`, error);
    }
  }
  return written;
}

/**
 * Writes the project into a user-picked folder (when supported) and starts a
 * local server for it. Returns where the site can be opened.
 */
export async function saveAndRunProject(
  code: Record<string, string>,
  siteName: string,
  overrides: Record<string, string> = {},
  sourceUrl: string = ""
): Promise<SaveRunResult> {
  const files = buildProjectFiles(code, siteName, overrides);
  if (files.length === 0) {
    throw new Error("Nothing to save yet — wait for the clone to finish.");
  }

  // The picker needs the click's user activation, so it opens first; the
  // files are written once the backend has copied the captured media in.
  let dir: FsDirectoryHandle | null = null;
  if (supportsFolderPicker()) {
    const picker = (window as PickerWindow).showDirectoryPicker;
    try {
      // `id` reuses the last location and permissions for this picker.
      dir = await picker!.call(window, { mode: "readwrite", id: "urltocode-sites" });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        throw new FolderPickCancelled("Folder selection cancelled");
      }
      console.warn("Folder picker failed, falling back to backend only", error);
    }
  }

  const saved = await mirrorToBackend(files, siteName, sourceUrl);

  let folderName: string | null = null;
  let fileCount = saved.files;
  if (dir) {
    try {
      fileCount = await writeProjectToDirectory(dir, files, saved);
      folderName = dir.name;
    } catch (error) {
      console.warn("Folder write failed, falling back to backend only", error);
    }
  }

  // A framework project needs `npm run dev`; its static preview copy is what
  // the backend can serve right away.
  const isFrameworkProject = files.some((file) => file.path.startsWith("preview/"));
  const projectUrl = `${HTTP_BACKEND_URL}${saved.url}`;
  const serveUrl = isFrameworkProject ? `${projectUrl}preview/` : projectUrl;
  return {
    savedToFolder: folderName !== null,
    isFrameworkProject,
    folderName,
    serveUrl,
    fileCount,
  };
}

/**
 * Downloads the whole project as one ZIP (pages, framework scaffold and the
 * captured images/videos). Works in every browser, unlike the folder picker.
 */
export async function downloadProjectZip(
  code: Record<string, string>,
  siteName: string,
  overrides: Record<string, string> = {}
): Promise<number> {
  const files = buildProjectFiles(code, siteName, overrides);
  if (files.length === 0) {
    throw new Error("Nothing to download yet — wait for the clone to finish.");
  }
  const saved = await mirrorToBackend(files, siteName);
  const link = document.createElement("a");
  link.href = `${HTTP_BACKEND_URL}/api/local-project/${encodeURIComponent(
    saved.runId
  )}/zip?name=${encodeURIComponent(siteName)}`;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  return saved.files;
}

/**
 * The clones this backend has already saved.
 *
 * A generated site outlives the tab that made it, and a tool that can only
 * show you the site you just built is a tool that makes you keep the
 * backend running to find it again.
 */
export interface SavedClone {
  runId: string;
  name: string;
  sourceUrl: string;
  savedAt: number;
  pageCount: number;
  hasServer: boolean;
  url: string;
  sizeBytes: number;
}

export async function fetchCloneLibrary(): Promise<SavedClone[]> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/local-project`);
  if (!response.ok) {
    throw new Error(`The clone library could not be read (${response.status}).`);
  }
  const body = (await response.json()) as { clones?: SavedClone[] };
  return body.clones ?? [];
}

/** Forget one saved clone and delete its files. */
export async function forgetClone(runId: string): Promise<void> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/local-project/${encodeURIComponent(runId)}`,
    { method: "DELETE" }
  );
  if (!response.ok) {
    throw new Error(`The clone could not be removed (${response.status}).`);
  }
}

/**
 * Downloads the project with the files its host needs.
 *
 * Not a deployment: it leaves the project ready to drop on a static host or
 * push to one that builds a container, which is the step we can honestly
 * do without a credential the user has not given us.
 */
export function downloadDeployBundle(runId: string, name: string): void {
  const link = document.createElement("a");
  link.href = `${HTTP_BACKEND_URL}/api/local-project/${encodeURIComponent(
    runId
  )}/deploy?name=${encodeURIComponent(name)}`;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/** A file size in words, for the library list. */
export function describeProjectSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * A project's own dev server, which hot-reloads when a file changes.
 *
 * Separate from the static preview on purpose: this runs model-generated
 * code on the user's machine, so it is started by an explicit action and
 * never by saving.
 */
export interface DevServerInfo {
  runId: string;
  running: boolean;
  url: string;
  startedAt: number;
  log: string[];
  error: string;
}

async function devRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${HTTP_BACKEND_URL}${path}`, init);
  if (!response.ok) {
    let detail = `status ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // keep the status-code detail
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function startDevServer(runId: string): Promise<DevServerInfo> {
  return devRequest<DevServerInfo>(`/api/local-project/${encodeURIComponent(runId)}/dev`, {
    method: "POST",
  });
}

export async function stopDevServer(runId: string): Promise<boolean> {
  const result = await devRequest<{ stopped: boolean }>(
    `/api/local-project/${encodeURIComponent(runId)}/dev`,
    { method: "DELETE" }
  );
  return result.stopped;
}

/** Push one edited file into the running project so the dev server reloads. */
export async function pushDevFile(
  runId: string,
  path: string,
  content: string
): Promise<void> {
  await devRequest<{ path: string }>(
    `/api/local-project/${encodeURIComponent(runId)}/dev/file`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content }),
    }
  );
}
