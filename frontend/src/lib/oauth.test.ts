import { listProviders } from "./oauth";

jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://localhost:7001",
  WS_BACKEND_URL: "ws://localhost:7001",
}));

describe("which sign-in buttons to draw", () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it("draws a button for each provider the server has set up", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ providers: ["github", "google"] }),
    }) as unknown as typeof fetch;

    expect(await listProviders()).toEqual(["github", "google"]);
  });

  it("draws none when the server has none", async () => {
    // A button for a provider that is not configured sends someone to the
    // provider's own error page and back, having promised something that was
    // never going to work.
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ providers: [] }),
    }) as unknown as typeof fetch;

    expect(await listProviders()).toEqual([]);
  });

  it("costs the buttons, not the form, when the server cannot be reached", async () => {
    global.fetch = jest
      .fn()
      .mockRejectedValue(new Error("network down")) as unknown as typeof fetch;

    expect(await listProviders()).toEqual([]);
  });

  it("costs the buttons on a bad answer too", async () => {
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      json: async () => ({}),
    }) as unknown as typeof fetch;

    expect(await listProviders()).toEqual([]);
  });
});