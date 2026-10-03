jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://backend.test",
  WS_BACKEND_URL: "ws://backend.test",
}));

import {
  deleteProject,
  listProjects,
  login,
  me,
  register,
  sessionToken,
  signOut,
  tokenForSocket,
} from "./accounts";

/**
 * What the user is shown has to match what the server will do.
 *
 * The rule under test is that the session travels with every call: the app
 * is served from a different port than the API, so a request made without
 * credentials is a request made as a stranger.
 *
 * jsdom is not installed here, so the two browser globals this module
 * reads are stood in for. Small enough to be honest: they hold a string
 * each, which is all the code asks of them.
 */

let cookies: Record<string, string> = {};
let stored: Record<string, string> = {};

beforeAll(() => {
  (global as Record<string, unknown>).document = {
    get cookie() {
      return Object.entries(cookies)
        .map(([name, value]) => `${name}=${value}`)
        .join("; ");
    },
    set cookie(raw: string) {
      const [pair] = raw.split(";");
      const index = pair.indexOf("=");
      cookies[pair.slice(0, index).trim()] = pair.slice(index + 1).trim();
    },
  };
  (global as Record<string, unknown>).localStorage = {
    getItem: (key: string) => (key in stored ? stored[key] : null),
    setItem: (key: string, value: string) => {
      stored[key] = value;
    },
    removeItem: (key: string) => {
      delete stored[key];
    },
  };
});

type Call = [string, RequestInit | undefined];

let calls: Call[] = [];

function stubFetch(body: unknown, ok = true): void {
  (global as { fetch?: unknown }).fetch = (url: string, init: RequestInit) => {
    calls.push([url, init]);
    return Promise.resolve({
      ok,
      status: ok ? 200 : 401,
      json: () => Promise.resolve(body),
    } as Response);
  };
}

beforeEach(() => {
  calls = [];
  cookies = {};
  stored = {};
});

afterEach(() => {
  delete (global as { fetch?: unknown }).fetch;
});

function sentCredentials(): RequestCredentials | undefined {
  return calls[0][1]?.credentials;
}

it("signs up with the session attached", async () => {
  stubFetch({
    account: { id: 1, email: "a@b.dev" },
    usage: { used: 0, remaining: 1, projects: 0, maxProjects: 1, tier: "free", resetsAt: 0 },
  });

  const result = await register("a@b.dev", "correct horse");

  expect(result.account.email).toBe("a@b.dev");
  expect(sentCredentials()).toBe("include");
});

it("signs in with the session attached", async () => {
  stubFetch({
    account: { id: 1, email: "a@b.dev" },
    usage: { used: 0, remaining: 1, projects: 0, maxProjects: 1, tier: "free", resetsAt: 0 },
  });

  await login("a@b.dev", "correct horse");

  expect(sentCredentials()).toBe("include");
});

it("keeps the session after signing out even if the server refuses", async () => {
  stubFetch({}, false);

  await signOut();

  // A cookie left behind would show the user as still signed in after
  // they asked not to be.
  expect(tokenForSocket()).toBeNull();
});

it("reads the cookie the server set", async () => {
  cookies = { utc_session: "abc.def.sig" };

  expect(tokenForSocket()).toBe("abc.def.sig");
});

it("falls back to the stored token when the cookie is gone", () => {
  // A cookie can be cleared while the app is open; the socket would then
  // silently become an anonymous one.
  stored = { "utc.session": "stored.token.sig" };

  expect(tokenForSocket()).toBe("stored.token.sig");
});

it("has no token to offer when nobody is signed in", async () => {
  expect(tokenForSocket()).toBeNull();
});

it("says who is signed out rather than failing", async () => {
  stubFetch({ account: null });

  expect((await me()).account).toBeNull();
});

it("keeps the token the server handed back on sign-up", async () => {
  // The cookie is HttpOnly, so this is the only way the page can hold a
  // token. Without it the websocket run is refused as an anonymous one.
  stubFetch({
    account: { id: 1, email: "a@b.dev" },
    usage: {},
    sessionToken: "1.1700000000.sig",
  });

  await register("a@b.dev", "correct horse");

  expect(sessionToken()).toBe("1.1700000000.sig");
});

it("restores the token on load so a reload stays signed in", async () => {
  stubFetch({
    account: { id: 1, email: "a@b.dev" },
    usage: {},
    sessionToken: "1.1700000001.sig",
  });

  await me();

  expect(sessionToken()).toBe("1.1700000001.sig");
});

it("does not invent a token when the server is signed out", async () => {
  stubFetch({ account: null, sessionToken: null });

  await me();

  expect(sessionToken()).toBeNull();
});

it("lists projects with the session attached", async () => {
  stubFetch({ projects: [], usage: {} });

  await listProjects();

  expect(sentCredentials()).toBe("include");
});

it("sends the delete as a delete", async () => {
  stubFetch({ projects: [], usage: {} });

  await deleteProject("run with space");

  expect(calls[0][0]).toContain("run%20with%20space");
  expect(calls[0][1]?.method).toBe("DELETE");
});

it("passes on what the server said it could not do", async () => {
  // The limit message is the whole point of the refusal.
  (global as { fetch?: unknown }).fetch = () =>
    Promise.resolve({
      ok: false,
      status: 402,
      json: () => Promise.resolve({ detail: "The free tier keeps 1 project." }),
    } as Response);

  await expect(
    (await import("./accounts")).saveProject("run-2", "Second", "https://x.test"),
  ).rejects.toThrow("free tier keeps 1 project");
});