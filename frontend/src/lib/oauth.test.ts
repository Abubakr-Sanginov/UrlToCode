import { listProviders } from "./oauth";

jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://localhost:7001",
  WS_BACKEND_URL: "ws://localhost:7001",
}));

function answering(body: unknown, ok = true, status = 200) {
  return jest.fn().mockResolvedValue({
    ok,
    status,
    json: async () => body,
  }) as unknown as typeof fetch;
}

describe("which sign-in buttons to draw", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("draws a button for each provider the server has set up", async () => {
    global.fetch = answering({ providers: ["github", "google"] });

    expect(await listProviders()).toEqual({
      kind: "ready",
      providers: ["github", "google"],
    });
  });

  it("says not-configured when the server has none", async () => {
    // A button for a provider that is not configured sends someone to the
    // provider's own error page and back, having promised something that was
    // never going to work.
    //
    // Not the same thing as unreachable. One is a matter of configuration and
    // the other of the request never arriving, and they need opposite fixes.
    global.fetch = answering({ providers: [] });

    expect(await listProviders()).toEqual({ kind: "not-configured" });
  });

  it("reports a blocked or offline request as unreachable, with the reason", async () => {
    // A cross-origin fetch refused by the browser rejects rather than
    // answering. The message it carries names the cause, and it is the one
    // piece of information that turns this from a dead end into a diagnosis.
    global.fetch = jest
      .fn()
      .mockRejectedValue(
        new TypeError("Failed to fetch")
      ) as unknown as typeof fetch;

    expect(await listProviders()).toEqual({
      kind: "unreachable",
      detail: "Failed to fetch",
    });
  });

  it("reports a bad status as unreachable", async () => {
    global.fetch = answering({}, false, 500);

    expect(await listProviders()).toEqual({
      kind: "unreachable",
      detail: "HTTP 500",
    });
  });

  it("notices a page where an answer was expected", async () => {
    // This is the one that actually happened: the site was asked instead of
    // the backend, answered with index.html, and the json parse failed. Every
    // version of this before reported it as a network blip, which is what
    // made it hard to find.
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON");
      },
    }) as unknown as typeof fetch;

    expect(await listProviders()).toEqual({
      kind: "unreachable",
      detail: "the response was not JSON",
    });
  });
});