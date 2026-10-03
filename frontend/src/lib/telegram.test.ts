jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://localhost:7001",
  WS_BACKEND_URL: "ws://localhost:7001",
}));

/**
 * Loaded fresh each time.
 *
 * The handle is remembered on purpose - Telegram's script is injected
 * before this app runs and does not appear later - but that memory is
 * exactly what a test setting up three different windows in a row must not
 * carry over from one to the next.
 */
function loadTelegram() {
  let module: typeof import("./telegram");
  jest.isolateModules(() => {
    module = require("./telegram");
  });
  return module!;
}

function pretendToBeTelegram(initData: string) {
  (global as unknown as { window: unknown }).window = {
    Telegram: {
      WebApp: {
        initData,
        initDataUnsafe: { user: { id: 777, first_name: "Ada" } },
        ready: jest.fn(),
        expand: jest.fn(),
      },
    },
  };
  return loadTelegram();
}

function pretendToBeABrowser() {
  (global as unknown as { window: unknown }).window = {};
  return loadTelegram();
}

describe("being inside Telegram", () => {
  afterEach(() => {
    jest.restoreAllMocks();
    delete (global as unknown as { window?: unknown }).window;
  });

  it("knows when it is not inside Telegram", () => {
    const telegram = pretendToBeABrowser();

    expect(telegram.isInsideTelegram()).toBe(false);
    expect(telegram.telegramWebApp()).toBeNull();
  });

  it("knows when it is", () => {
    expect(pretendToBeTelegram("auth_date=1&hash=abc").isInsideTelegram()).toBe(true);
  });

  it("does not count an empty blob as being inside", () => {
    // Outside Telegram the script runs and hands over an empty string. If
    // that counted, the ordinary browser would try to sign in with nothing.
    expect(pretendToBeTelegram("").isInsideTelegram()).toBe(false);
  });

  it("hands the signed blob to the server rather than deciding itself", async () => {
    // A Mini App is an ordinary web page: it could claim to be anybody.
    // What it cannot do is sign the blob, which is why the blob is what
    // gets sent and the answer comes back from the server.
    const telegram = pretendToBeTelegram("auth_date=1&hash=abc");
    global.fetch = jest.fn().mockResolvedValue({ ok: true }) as unknown as typeof fetch;

    const signedIn = await telegram.signInThroughTelegram();

    expect(signedIn).toBe(true);
    const [, options] = (global.fetch as jest.Mock).mock.calls[0];
    expect(JSON.parse((options as RequestInit).body as string).initData).toBe(
      "auth_date=1&hash=abc"
    );
  });

  it("reports failure rather than throwing when Telegram is unreachable", async () => {
    // Someone opening the app in a normal browser must get the ordinary
    // sign-in, not an error about Telegram being down.
    const telegram = pretendToBeTelegram("auth_date=1&hash=abc");
    global.fetch = jest.fn().mockRejectedValue(new Error("offline")) as unknown as typeof fetch;

    expect(await telegram.signInThroughTelegram()).toBe(false);
  });

  it("reports a refusal rather than pretending it worked", async () => {
    const telegram = pretendToBeTelegram("forged");
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 401 }) as unknown as typeof fetch;

    expect(await telegram.signInThroughTelegram()).toBe(false);
  });

  it("does nothing at all outside Telegram", async () => {
    const telegram = pretendToBeABrowser();
    global.fetch = jest.fn() as unknown as typeof fetch;

    expect(await telegram.signInThroughTelegram()).toBe(false);
    expect(global.fetch).not.toHaveBeenCalled();
  });
});