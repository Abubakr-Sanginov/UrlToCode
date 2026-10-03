jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://backend.test",
  WS_BACKEND_URL: "ws://backend.test",
}));

import {
  useAccountStore,
  ensureAccountLoaded,
} from "../store/account-store";
import { useAccountUi } from "../store/account-ui-store";
import { storeSessionToken, sessionToken } from "../lib/accounts";

function reset() {
  useAccountStore.setState({
    account: null,
    usage: null,
    loading: false,
    error: "",
  });
}

function stubFetchOnce(body: unknown) {
  const fetchMock = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => body,
  });
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

beforeEach(() => {
  reset();
  // There is no jsdom here, so the browser storage the session lives in is
  // stood up by hand, exactly as the accounts test does.
  const stored: Record<string, string> = {};
  (global as Record<string, unknown>).localStorage = {
    getItem: (key: string) => (key in stored ? stored[key] : null),
    setItem: (key: string, value: string) => {
      stored[key] = value;
    },
    removeItem: (key: string) => {
      delete stored[key];
    },
    clear: () => {
      Object.keys(stored).forEach((key) => delete stored[key]);
    },
  };
});

describe("one account for the whole app", () => {
  it("shows who is signed in to every part of the app at once", async () => {
    // The bug this store exists to prevent: the icon strip signed in, the
    // clone pane never learned about it, and the first run was refused as
    // if nobody were signed in.
    stubFetchOnce({
      account: { id: 7, email: "a@b.dev" },
      usage: { used: 0, remaining: 1, projects: 0, maxProjects: 1, tier: "free" },
      sessionToken: "7.1700000000.sig",
    });

    const ok = await useAccountStore
      .getState()
      .signIn("a@b.dev", "correct horse", true);

    expect(ok).toBe(true);
    // Every reader goes through the same selectors.
    expect(useAccountStore.getState().account?.email).toBe("a@b.dev");
    expect(useAccountStore.getState().usage?.remaining).toBe(1);
  });

  it("reaches the same state through a reader that mounted earlier", async () => {
    // A component that read the store before the sign-in sees the sign-in,
    // because there is nothing stale to hold on to.
    const seenBefore = useAccountStore.getState().account;
    stubFetchOnce({
      account: { id: 7, email: "a@b.dev" },
      usage: { used: 0, remaining: 1, projects: 0, maxProjects: 1, tier: "free" },
      sessionToken: "7.1700000000.sig",
    });
    await useAccountStore.getState().signIn("a@b.dev", "correct horse", true);

    expect(seenBefore).toBeNull();
    expect(useAccountStore.getState().account?.id).toBe(7);
  });

  it("keeps the token a signed-in browser needs for its run", async () => {
    stubFetchOnce({
      account: { id: 7, email: "a@b.dev" },
      usage: { used: 0, remaining: 1, projects: 0, maxProjects: 1, tier: "free" },
      sessionToken: "7.1700000000.sig",
    });

    await useAccountStore.getState().signIn("a@b.dev", "correct horse", true);

    expect(sessionToken()).toBe("7.1700000000.sig");
  });

  it("forgets everything on sign-out, token included", async () => {
    storeSessionToken("7.1700000000.sig");
    useAccountStore.setState({ account: { id: 7, email: "a@b.dev" } });
    stubFetchOnce({ ok: true });

    await useAccountStore.getState().signOut();

    expect(useAccountStore.getState().account).toBeNull();
    expect(sessionToken()).toBeNull();
  });

  it("asks the server once, however many parts of the app want to know", () => {
    // Before this was a store, each component fetched on mount: four
    // readers, four round trips, four chances to disagree.
    const fetchMock = stubFetchOnce({ account: null });
    ensureAccountLoaded();
    ensureAccountLoaded();
    ensureAccountLoaded();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("where the account lives", () => {
  it("opens the sign-in dialog from anywhere", () => {
    useAccountUi.getState().openSignIn();
    expect(useAccountUi.getState().isSignInOpen).toBe(true);
  });

  it("opens on the form the button is asking for", () => {
    // A button reading "Sign in" must not open a form that posts to
    // /auth/register. The account that already exists is told the email is
    // taken, and the session is never established - which looks exactly
    // like signing in not working.
    useAccountUi.getState().openSignIn();
    expect(useAccountUi.getState().signInMode).toBe("signin");

    useAccountUi.getState().openRegister();
    expect(useAccountUi.getState().signInMode).toBe("register");

    // And the other way back, so a returning click is not left on the
    // register form from the last time someone arrived.
    useAccountUi.getState().openSignIn();
    expect(useAccountUi.getState().signInMode).toBe("signin");
  });

  it("closes the sign-in dialog and does not leave the panel open", () => {
    useAccountUi.setState({ isPanelOpen: true });
    useAccountUi.getState().openSignIn();
    expect(useAccountUi.getState().isPanelOpen).toBe(false);
  });

  it("toggles the account panel", () => {
    useAccountUi.getState().togglePanel();
    expect(useAccountUi.getState().isPanelOpen).toBe(true);
    useAccountUi.getState().togglePanel();
    expect(useAccountUi.getState().isPanelOpen).toBe(false);
  });
});