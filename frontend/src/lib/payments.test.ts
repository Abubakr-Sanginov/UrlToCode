import { buyPlan } from "./payments";

jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://localhost:7001",
  WS_BACKEND_URL: "ws://localhost:7001",
}));

/**
 * Every test here stands up its own copy of the module, because the handle
 * to Telegram is remembered on purpose and a suite that reuses it across
 * three different pretend windows proves nothing.
 */
function load() {
  let module: typeof import("./payments");
  jest.isolateModules(() => {
    module = require("./payments");
  });
  return module!;
}

function insideTelegram(startParam?: string, withInvoices = true) {
  (global as unknown as { window: unknown }).window = {
    Telegram: {
      WebApp: {
        initData: "auth_date=1&hash=abc",
        initDataUnsafe: startParam ? { start_param: startParam } : {},
        ...(withInvoices ? { openInvoiceLink: jest.fn() } : {}),
      },
    },
  };
}

describe("paying in Stars", () => {
  afterEach(() => {
    jest.restoreAllMocks();
    delete (global as unknown as { window?: unknown }).window;
  });

  it("sends what Telegram signed, not what the page claims", async () => {
    insideTelegram();
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ url: "https://t.me/invoice/x", tier: "pro" }),
    }) as unknown as typeof fetch;

    await buyPlan("pro");

    const [, options] = (global.fetch as jest.Mock).mock.calls[0];
    // "I would like to pay" identifies nobody. The signed blob does.
    expect((options as RequestInit).headers).toMatchObject({
      "X-Telegram-Auth": "auth_date=1&hash=abc",
    });
  });

  it("opens Telegram's own payment sheet", async () => {
    // A payment screen drawn by this app would be one that could be edited.
    insideTelegram();
    const openInvoiceLink = jest.fn();
    (global as unknown as { window: unknown }).window = {
      Telegram: {
        WebApp: {
          initData: "signed",
          initDataUnsafe: {},
          openInvoiceLink,
        },
      },
    };
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ url: "https://t.me/invoice/x" }),
    }) as unknown as typeof fetch;

    await load().buyPlan("pro");

    expect(openInvoiceLink).toHaveBeenCalledWith("https://t.me/invoice/x");
  });

  it("says no rather than throwing when there is no payment sheet", async () => {
    insideTelegram(undefined, false);
    global.fetch = jest.fn() as unknown as typeof fetch;

    // A person opening the site in a normal browser gets a plain "open it
    // in Telegram", not an error about something they cannot see.
    expect(await load().buyPlan("pro")).toBe(false);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("reports a refusal from the server", async () => {
    insideTelegram();
    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: "That is not Telegram." }),
    }) as unknown as typeof fetch;

    await expect(load().buyPlan("pro")).rejects.toThrow("That is not Telegram.");
  });
});

describe("opening the project a link pointed at", () => {
  afterEach(() => {
    jest.restoreAllMocks();
    delete (global as unknown as { window?: unknown }).window;
  });

  it("reads the project out of a start parameter", () => {
    insideTelegram("project_run-123");

    expect(load().startProject()).toBe("run-123");
  });

  it("says nothing when there was no start parameter", () => {
    insideTelegram();

    expect(load().startProject()).toBeNull();
  });

  it("ignores a parameter meant for something else", () => {
    // One parameter will carry several kinds of link. A share link is not a
    // project, and must not be opened as one.
    insideTelegram("share_abc123");

    expect(load().startProject()).toBeNull();
  });

  it("refuses an absurdly long value", () => {
    insideTelegram("project_" + "x".repeat(60));

    expect(load().startProject()).toBeNull();
  });

  it("says nothing outside Telegram", () => {
    (global as unknown as { window: unknown }).window = {};

    expect(load().startProject()).toBeNull();
  });
});