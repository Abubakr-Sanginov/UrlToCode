/**
 * Signing in with GitHub or Google.
 *
 * The only thing this needs from the server is which buttons to draw. A
 * button for a provider that is not configured would send someone to
 * GitHub's own error page and back again, having told them something that
 * was never going to work.
 */
import { HTTP_BACKEND_URL } from "../config";

export async function listProviders(): Promise<string[]> {
  try {
    const response = await fetch(`${HTTP_BACKEND_URL}/api/auth/providers`, {
      credentials: "include",
    });
    if (!response.ok) return [];
    const body = (await response.json()) as { providers?: string[] };
    return body.providers ?? [];
  } catch {
    // A network blip here should cost the two extra buttons, not the
    // password form that works without them.
    return [];
  }
}