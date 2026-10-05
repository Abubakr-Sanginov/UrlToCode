import { HTTP_BACKEND_URL } from "../config";

/**
 * The account this browser is signed in as, and what it may still do.
 *
 * `remaining` is the number the user reads, so it is the first field here
 * rather than something each screen works out for itself.
 */
export interface Usage {
  used: number;
  remaining: number;
  projects: number;
  maxProjects: number;
  tier: string;
  resetsAt: number;
}

export interface Account {
  id: number;
  email: string;
}

export interface Project {
  runId: string;
  name: string;
  sourceUrl: string;
  savedAt: number;
}

interface MeResponse {
  account: Account | null;
  usage?: Usage;
  /**
   * The signed session, handed back by the server on sign-in and on every
   * load. The cookie is HttpOnly, so the page cannot read it, and a
   * websocket cannot carry one: this is how the run stays signed in.
   */
  sessionToken?: string | null;
}

const SESSION_KEY = "utc.session";

/** The name the session arrives under, in the part of an address the browser
 *  keeps to itself. Matches SESSION_FRAGMENT in backend/routes/oauth.py. */
export const SESSION_FRAGMENT = "utc_session";

/**
 * Pick a session up out of the address after signing in with a provider.
 *
 * The server ends its OAuth redirect with `#utc_session=<token>`. The
 * fragment is the one part of an address the browser does not send to a
 * server, so the token does not end up in a log or a Referer.
 *
 * Without this the session has to be a cookie set on the backend's address
 * and read again by a page on the site's address - a third-party cookie,
 * which browsers increasingly refuse to send no matter what SameSite says.
 * That failure is invisible: the account is created, the cookie is set
 * correctly, and the person lands back not signed in.
 *
 * Run before anything reads the session, so the very first request after
 * coming back already carries it.
 */
export function adoptSessionFromAddress(): void {
  const hash = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : "";
  if (!hash) return;
  const found = new URLSearchParams(hash).get(SESSION_FRAGMENT);
  if (!found) return;
  remember(found);
  // Taken off the address at once: it stays in the history, in the bar, and
  // in anything that screenshots the page.
  window.history.replaceState({}, "", window.location.pathname + window.location.search);
}

/**
 * The signed session, kept where a websocket can reach it.
 *
 * Browsers refuse to put a cookie on a websocket handshake to another
 * origin, and the API is on a different port from the app, so the token is
 * read from here and sent in the socket's first message instead.
 */
export function sessionToken(): string | null {
  return localStorage.getItem(SESSION_KEY);
}

function remember(token: string | null): void {
  if (token === null) {
    localStorage.removeItem(SESSION_KEY);
  } else {
    localStorage.setItem(SESSION_KEY, token);
  }
}

/**
 * The session, sent as a header when there is one to send.
 *
 * Sent alongside the cookie rather than instead of it: a browser that still
 * passes the cookie along does not need this, and one that has stopped
 * passing it has nothing else. The server prefers the cookie when both are
 * present.
 */
function sessionHeaders(): Record<string, string> {
  const token = sessionToken();
  return token ? { "X-Session-Token": token } : {};
}

/**
 * A cookie is set by the server as well, so the two cannot drift: this
 * prefers the cookie and falls back to the stored token.
 */
function tokenFromCookie(): string | null {
  const match = document.cookie.match(/(?:^|;\s*)utc_session=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function readError(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (body && typeof body.detail === "string" && body.detail) {
      return body.detail;
    }
  } catch {
    // A body that is not JSON is still an error; the status line is the
    // best that can be said about it.
  }
  return `${fallback} (${response.status})`;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${HTTP_BACKEND_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...sessionHeaders() },
    // The session is a cookie, and the API is on another port.
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await readError(response, "That did not work."));
  }
  return (await response.json()) as T;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${HTTP_BACKEND_URL}${path}`, {
    credentials: "include",
    headers: sessionHeaders(),
  });
  if (!response.ok) {
    throw new Error(await readError(response, "That did not work."));
  }
  return (await response.json()) as T;
}

export interface SignInResult {
  account: Account;
  usage: Usage;
  sessionToken?: string | null;
}

export async function register(
  email: string,
  password: string,
): Promise<SignInResult> {
  const result = await post<SignInResult>("/api/auth/register", {
    email,
    password,
  });
  remember(result.sessionToken ?? null);
  return result;
}

export async function login(
  email: string,
  password: string,
): Promise<SignInResult> {
  const result = await post<SignInResult>("/api/auth/login", { email, password });
  remember(result.sessionToken ?? null);
  return result;
}

export async function signOut(): Promise<void> {
  try {
    await post<{ ok: boolean }>("/api/auth/logout", {});
  } catch {
    // Swallowed on purpose. The local session is what this browser shows,
    // and a server that refused the call cannot change that: raising here
    // would leave the user looking signed in, with an error about signing
    // out and no way to make it go away.
  } finally {
    // Forgetting locally whatever the server says: a sign-out that leaves
    // the app looking signed in is worse than a stray cookie.
    remember(null);
  }
}

export async function me(): Promise<MeResponse> {
  const result = await get<MeResponse>("/api/me");
  // Keep the stored token in step with the cookie. Without this a reload
  // leaves the page holding a token while the cookie has moved on, and
  // the two disagree about who is signed in.
  if (result.sessionToken !== undefined) {
    remember(result.sessionToken);
  }
  return result;
}

export async function listProjects(): Promise<{
  projects: Project[];
  usage: Usage;
}> {
  return get<{ projects: Project[]; usage: Usage }>("/api/projects");
}

export async function saveProject(
  runId: string,
  name: string,
  sourceUrl: string,
): Promise<{ projects: Project[]; usage: Usage }> {
  return post<{ projects: Project[]; usage: Usage }>("/api/projects", {
    runId,
    name,
    sourceUrl,
  });
}

/**
 * The code behind one of this account's projects, ready to open.
 *
 * Asked of the server by run id, which the server checks against the same
 * project list it showed: knowing a run id is not permission to read it.
 */
export interface OpenedProject {
  runId: string;
  baseUrl: string;
  stack: string;
  phase: string;
  code: Record<string, string>;
}

export async function openProject(runId: string): Promise<OpenedProject> {
  return get<OpenedProject>(
    `/api/projects/${encodeURIComponent(runId)}`
  );
}

export async function deleteProject(
  runId: string,
): Promise<{ projects: Project[]; usage: Usage }> {
  const response = await fetch(
    `${HTTP_BACKEND_URL}/api/projects/${encodeURIComponent(runId)}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!response.ok) {
    throw new Error(await readError(response, "That did not work."));
  }
  return (await response.json()) as { projects: Project[]; usage: Usage };
}

/**
 * The token to send with a websocket handshake's first message.
 *
 * Kept here so the socket and the REST calls agree on who is signed in:
 * two different answers would let the UI show a balance the server then
 * refuses to honour.
 */
export function tokenForSocket(): string | null {
  return tokenFromCookie() ?? sessionToken();
}

export function storeSessionToken(token: string | null): void {
  remember(token);
}