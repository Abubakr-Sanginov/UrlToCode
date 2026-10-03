jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://backend.test",
  WS_BACKEND_URL: "ws://backend.test",
}));

import {
  adminToken,
  fetchAccounts,
  forgetAdminToken,
  rememberAdminToken,
  resetLimits,
  saveLimits,
  setTier,
} from "./admin";

/**
 * The admin token opens every account on the server, so where it is kept
 * matters as much as that it is checked.
 */

function stubFetch(response: { ok: boolean; status?: number; body?: unknown }) {
  const fetchMock = jest.fn().mockResolvedValue({
    ok: response.ok,
    status: response.status ?? 200,
    json: async () => response.body ?? {},
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

beforeEach(() => {
  const stored: Record<string, string> = {};
  (global as Record<string, unknown>).sessionStorage = {
    getItem: (key: string) => (key in stored ? stored[key] : null),
    setItem: (key: string, value: string) => {
      stored[key] = value;
    },
    removeItem: (key: string) => {
      delete stored[key];
    },
  };
});

it("starts with no token", () => {
  expect(adminToken()).toBe("");
});

it("keeps the token for the tab, not for good", () => {
  // A token that outlived the tab would still be sitting in localStorage
  // the next time the browser is opened on this machine.
  rememberAdminToken("secret");
  expect(adminToken()).toBe("secret");

  forgetAdminToken();
  expect(adminToken()).toBe("");
});

it("sends the token as a header rather than in the query string", () => {
  // A token in a URL ends up in logs and in the referrer of every
  // request made from the page.
  const fetchMock = stubFetch({ ok: true, body: { accounts: [], tiers: {} } });

  void fetchAccounts("secret");

  const [, init] = fetchMock.mock.calls[0];
  expect((init.headers as Record<string, string>)["X-Admin-Token"]).toBe("secret");
  expect(fetchMock.mock.calls[0][0]).not.toContain("secret");
});

it("reports a refused token in the server's own words", async () => {
  stubFetch({ ok: false, status: 401, body: { detail: "That admin token is not right." } });

  await expect(fetchAccounts("guess")).rejects.toThrow(
    "That admin token is not right."
  );
});

it("sends raised limits to the account they were raised for", async () => {
  const fetchMock = stubFetch({ ok: true, body: { id: 7 } });

  await saveLimits("secret", 7, { dailyActions: 50, maxProjects: null, note: "paid" });

  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("http://backend.test/api/admin/accounts/7/limits");
  expect(init.method).toBe("POST");
  expect(JSON.parse(init.body as string)).toEqual({
    dailyActions: 50,
    maxProjects: null,
    note: "paid",
  });
});

it("asks for the tier back when the override is cleared", async () => {
  const fetchMock = stubFetch({ ok: true, body: { id: 7 } });

  await resetLimits("secret", 7);

  expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
});

it("can move an account between tiers", async () => {
  const fetchMock = stubFetch({ ok: true, body: { id: 7 } });

  await setTier("secret", 7, "studio");

  expect(fetchMock.mock.calls[0][0]).toBe(
    "http://backend.test/api/admin/accounts/7/tier"
  );
});

it("is not remembered once the tab closes", () => {
  // The panel shows the admin link only to someone holding a token, so
  // this is what keeps the link off every other account's panel.
  rememberAdminToken("secret");
  expect(adminToken().length).toBeGreaterThan(0);

  forgetAdminToken();
  expect(adminToken()).toBe("");
});
