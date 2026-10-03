import { HTTP_BACKEND_URL } from "../config";

/**
 * What the backend is keeping from previous generations.
 *
 * Cloning the same site twice should not cost twice, so finished pages are
 * kept on the backend keyed by everything that decides what the model would
 * say. That is invisible to the user, and an invisible cache that hands back
 * an old answer is indistinguishable from a broken tool - so it is reported
 * here, and can be emptied.
 */

export interface CloneCacheInfo {
  entries: number;
  bytes: number;
  /** Epoch seconds of the oldest entry, or 0 when the cache is empty. */
  oldest: number;
  directory: string;
}

export class CloneCacheError extends Error {}

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // A non-JSON error body is common enough to fall through.
  }
  return `Request failed (${response.status})`;
}

export async function fetchCloneCache(): Promise<CloneCacheInfo> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/clone-cache`);
  if (!response.ok) throw new CloneCacheError(await readError(response));
  const body = (await response.json()) as Partial<CloneCacheInfo>;
  return {
    entries: body.entries ?? 0,
    bytes: body.bytes ?? 0,
    oldest: body.oldest ?? 0,
    directory: body.directory ?? "",
  };
}

/** Empty the cache, answering how many entries went. */
export async function clearCloneCache(): Promise<{ removed: number }> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/clone-cache`, {
    method: "DELETE",
  });
  if (!response.ok) throw new CloneCacheError(await readError(response));
  const body = (await response.json()) as { removed?: number };
  return { removed: body.removed ?? 0 };
}

/** A cache size in words, because "4,096 bytes" helps nobody. */
export function describeCacheSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
