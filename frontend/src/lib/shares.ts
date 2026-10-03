/**
 * Sending someone a link to a cloned site.
 *
 * The link opens the site, not the code: the server serves the same
 * laid-out document the preview pane shows, so a Next.js or Vite project
 * needs no build and the visitor waits for nothing.
 *
 * What is left of the allowance comes from the server rather than from a
 * count held here. The button can be hidden in this tab; what decides
 * whether another link is made is checked where it is issued.
 */
import { HTTP_BACKEND_URL } from "../config";

export interface Share {
  token: string;
  runId: string;
  pagePath: string;
  createdAt: number;
}

export interface ShareUsage {
  used: number;
  limit: number;
  remaining: number;
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body && typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // Not JSON: the status line is the best that can be said about it.
  }
  return `${fallback} (${response.status})`;
}

export function shareUrl(token: string): string {
  return `${window.location.origin}/s/${token}`;
}

export async function listShares(): Promise<{
  shares: Share[];
  usage: ShareUsage;
}> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/shares`, {
    credentials: "include",
  });
  if (!response.ok) throw new Error(await readError(response, "That did not work."));
  return (await response.json()) as { shares: Share[]; usage: ShareUsage };
}

export async function createShare(
  runId: string,
  pagePath: string = "/"
): Promise<Share & { usage: ShareUsage }> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/shares`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ runId, pagePath }),
  });
  if (!response.ok) throw new Error(await readError(response, "That did not work."));
  const made = (await response.json()) as Share & {
    usedThisMonth: number;
    limitPerMonth: number;
    remainingThisMonth: number;
  };
  return {
    ...made,
    usage: {
      used: made.usedThisMonth,
      limit: made.limitPerMonth,
      remaining: made.remainingThisMonth,
    },
  };
}

export async function revokeShare(token: string): Promise<void> {
  const response = await fetch(`${HTTP_BACKEND_URL}/api/shares/${token}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!response.ok) throw new Error(await readError(response, "That did not work."));
}