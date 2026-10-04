/**
 * Signing in with GitHub or Google.
 *
 * The only thing this needs from the server is which buttons to draw. A
 * button for a provider that is not configured would send someone to
 * GitHub's own error page and back again, having told them something that
 * was never going to work.
 *
 * A failure here is reported as *which* failure, not just that there was
 * one. "Signing in is not available" covers a server with no keys set and a
 * request that never got there at all, and those need opposite fixes — one
 * is configuration, the other is a stale bundle or a blocked cross-origin
 * call. Telling them apart is the difference between an afternoon and a
 * guess.
 */
import { HTTP_BACKEND_URL } from "../config";

export type ProvidersResult =
  | { kind: "ready"; providers: string[] }
  /** The server answered, and has no keys configured for any provider. */
  | { kind: "not-configured" }
  /** The question was never answered: offline, blocked, or the wrong URL. */
  | { kind: "unreachable"; detail: string };

export async function listProviders(): Promise<ProvidersResult> {
  let response: Response;
  try {
    response = await fetch(`${HTTP_BACKEND_URL}/api/auth/providers`, {
      credentials: "include",
    });
  } catch (error) {
    // A cross-origin fetch blocked by the browser rejects rather than
    // answering, and its message names the reason - which is the one thing
    // worth showing when this is the thing that is broken.
    return {
      kind: "unreachable",
      detail: error instanceof Error ? error.message : String(error),
    };
  }
  if (!response.ok) {
    return { kind: "unreachable", detail: `HTTP ${response.status}` };
  }
  try {
    const body = (await response.json()) as { providers?: string[] };
    const providers = body.providers ?? [];
    return providers.length === 0
      ? { kind: "not-configured" }
      : { kind: "ready", providers };
  } catch {
    // A body that is not json means the address answered, but with a page
    // rather than an answer - the site was asked instead of the backend.
    return { kind: "unreachable", detail: "the response was not JSON" };
  }
}