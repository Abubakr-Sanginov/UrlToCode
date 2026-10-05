/**
 * A session that comes back in the address rather than in a cookie.
 *
 * Sign-in worked and then lost the session on the way home: the account was
 * created, the cookie was set correctly with SameSite=None and Secure, and
 * the person landed back on the site not signed in. The cookie belongs to
 * the backend's address and has to be read again by a page on the site's
 * address, which makes it a third-party cookie.
 *
 * So the callback leaves the token in the fragment. This is the half of that
 * the page is responsible for: reading it before anything asks who is
 * signed in, and taking it off the address afterwards.
 */

import { adoptSessionFromAddress } from "./accounts";

jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://localhost:7001",
  WS_BACKEND_URL: "ws://localhost:7001",
}));

const KEY = "utc.session";

/**
 * jsdom is not installed here, so the three browser globals this touches are
 * stood in for: enough of location and history to change an address and read
 * it back, and a localStorage that holds strings.
 */
let stored: Record<string, string> = {};
let address = { pathname: "/", search: "", hash: "" };

beforeAll(() => {
  (global as Record<string, unknown>).localStorage = {
    getItem: (key: string) => (key in stored ? stored[key] : null),
    setItem: (key: string, value: string) => {
      stored[key] = value;
    },
    removeItem: (key: string) => {
      delete stored[key];
    },
  };
  (global as Record<string, unknown>).window = {
    get location() {
      return address;
    },
    history: {
      replaceState: (_state: unknown, _title: string, url: string) => {
        // Only what the code under test writes back: the path, the query and
        // the fragment. Nothing here pretends to be a browser.
        const [pathAndQuery, fragment] = url.split("#");
        const [pathname, search] = (pathAndQuery || "/").split("?");
        address = {
          pathname: pathname || "/",
          search: search ? `?${search}` : "",
          hash: fragment ? `#${fragment}` : "",
        };
      },
    },
  };
});

function landOn(hash: string): void {
  (window as unknown as { history: { replaceState: (s: unknown, t: string, u: string) => void } }).history.replaceState(
    {},
    "",
    hash ? `/${hash}` : "/"
  );
}

describe("adopting a session from the address", () => {
  beforeEach(() => {
    stored = {};
    landOn("");
  });

  afterEach(() => {
    stored = {};
  });

  it("keeps the session the server left in the fragment", () => {
    landOn("#utc_session=99.1700000000.signature");

    adoptSessionFromAddress();

    expect(localStorage.getItem(KEY)).toBe("99.1700000000.signature");
  });

  it("takes it off the address afterwards", () => {
    // It stays in the history, in the address bar, and in anything that
    // screenshots the page. A session is not something to leave lying about.
    landOn("#utc_session=99.1700000000.signature");

    adoptSessionFromAddress();

    expect(window.location.hash).toBe("");
  });

  it("leaves an address with nothing in it alone", () => {
    landOn("#some-other-thing=1");

    adoptSessionFromAddress();

    expect(localStorage.getItem(KEY)).toBeNull();
    expect(window.location.hash).toBe("#some-other-thing=1");
  });

  it("does not throw when there is no fragment at all", () => {
    landOn("");

    expect(() => adoptSessionFromAddress()).not.toThrow();
  });

  it("is not undone by a second call", () => {
    // The page can mount more than once, and an effect that runs twice must
    // not drop what the first one just picked up.
    landOn("#utc_session=99.1700000000.signature");

    adoptSessionFromAddress();
    adoptSessionFromAddress();

    expect(localStorage.getItem(KEY)).toBe("99.1700000000.signature");
  });
});