import { createShare, listShares, revokeShare, shareUrl } from "./shares";

jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://localhost:7001",
  WS_BACKEND_URL: "ws://localhost:7001",
}));

describe("making a link to send someone", () => {
  beforeEach(() => {
    // No jsdom here, so the one piece of the browser this needs is stood in
    // for by hand: the address the app is being served from.
    (global as unknown as { window: unknown }).window = {
      location: { origin: "http://localhost:5173" },
    };
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("points at this app, so the link works wherever the app is served", () => {
    // The reader has no account and no cookie, so the link cannot rely on
    // anything about who opens it - only on the address.
    expect(shareUrl("abc123")).toBe(`${window.location.origin}/s/abc123`);
  });

  it("takes what the server says is left, not a number from this tab", () => {
    // The allowance is decided where the link is issued. A count kept here
    // can be edited in this tab; one read back from the server cannot.
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        token: "abc123",
        runId: "run-1",
        pagePath: "/",
        createdAt: 1,
        usedThisMonth: 1,
        limitPerMonth: 1,
        remainingThisMonth: 0,
      }),
    }) as unknown as typeof fetch;

    return createShare("run-1").then((made) => {
      expect(made.usage).toEqual({ used: 1, limit: 1, remaining: 0 });
    });
  });

  it("says what the server refused and why", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: "This plan makes 1 link a month." }),
    }) as unknown as typeof fetch;

    await expect(createShare("run-1")).rejects.toThrow(
      "This plan makes 1 link a month."
    );
  });

  it("reports an empty allowance as zero, not as missing", async () => {
    // The free plan has no sharing at all. Showing that as "no limit" would
    // offer the button to someone who cannot use it.
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ shares: [], usage: { used: 0, limit: 0, remaining: 0 } }),
    }) as unknown as typeof fetch;

    const result = await listShares();

    expect(result.usage).toEqual({ used: 0, limit: 0, remaining: 0 });
  });

  it("takes a link down", async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: true }) as unknown as typeof fetch;

    await revokeShare("abc123");

    expect(global.fetch).toHaveBeenCalledWith(
      "http://localhost:7001/api/shares/abc123",
      expect.objectContaining({ method: "DELETE" })
    );
  });
});