import { create } from "zustand";
import {
  Account,
  Usage,
  login as loginRequest,
  me,
  register as registerRequest,
  signOut as signOutRequest,
} from "../lib/accounts";
import { isInsideTelegram, prepareTelegram, signInThroughTelegram } from "../lib/telegram";

/**
 * The signed-in account, held once for the whole app.
 *
 * This was a hook that fetched on mount, so every component using it
 * fetched its own copy and knew nothing of the others: signing in from the
 * icon strip left the clone pane still reading "Sign in to clone", and the
 * first click of a run was refused as if nobody were signed in. One store
 * means one answer.
 */
interface AccountStore {
  account: Account | null;
  usage: Usage | null;
  loading: boolean;
  error: string;
  refresh: () => Promise<Account | null>;
  signIn: (email: string, password: string, asNewAccount: boolean) => Promise<boolean>;
  signOut: () => Promise<void>;
  resetError: () => void;
}

export const useAccountStore = create<AccountStore>((set) => ({
  account: null,
  usage: null,
  loading: true,
  error: "",

  refresh: async () => {
    try {
      const result = await me();
      set({
        account: result.account,
        usage: result.usage ?? null,
        loading: false,
        error: "",
      });
      return result.account;
    } catch {
      set({ account: null, usage: null, loading: false, error: "" });
      return null;
    }
  },

  signIn: async (email, password, asNewAccount) => {
    set({ error: "", loading: true });
    try {
      const result = asNewAccount
        ? await registerRequest(email, password)
        : await loginRequest(email, password);
      set({
        account: result.account,
        usage: result.usage,
        loading: false,
        error: "",
      });
      return true;
    } catch (error) {
      set({
        loading: false,
        error: error instanceof Error ? error.message : "That did not work.",
      });
      return false;
    }
  },

  signOut: async () => {
    await signOutRequest();
    set({ account: null, usage: null, loading: false, error: "" });
  },

  resetError: () => set({ error: "" }),
}));

/** Loaded once, when the app opens. */
let started = false;
export function ensureAccountLoaded(): void {
  if (started) return;
  started = true;
  void bootstrap();
}

/**
 * Signing in from inside Telegram, then loading as usual.
 *
 * Both halves are needed and the order is fixed. Inside Telegram there is
 * no cookie yet, so the first ask of "who am I" would come back empty and
 * the page would show a sign-in dialog to somebody who is already signed
 * in. Outside Telegram the first ask succeeds on its own and the second is
 * skipped.
 */
async function bootstrap(): Promise<void> {
  prepareTelegram();

  // Only inside Telegram. Outside it the ordinary cookie answers
  // immediately and there is nothing to ask for.
  if (isInsideTelegram()) {
    await signInThroughTelegram();
  }
  await useAccountStore.getState().refresh();
}
