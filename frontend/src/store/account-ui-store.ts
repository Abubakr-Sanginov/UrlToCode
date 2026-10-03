import { create } from "zustand";

/**
 * Where the account lives in the UI.
 *
 * Its own store rather than prop-drilling: the clone pane, the icon strip
 * and the account popover all need to reach the same two pieces of state,
 * and they are siblings far apart in the tree.
 */
interface AccountUiStore {
  isSignInOpen: boolean;
  /**
   * Which form the dialog opens on.
   *
   * Held here because the button that opens it is the only thing that knows
   * the answer. A button reading "Sign in" must not open a form that posts
   * to /auth/register: an existing account is told the email is taken, which
   * reads as "signing in does not work" rather than as "you are on the wrong
   * form" - and the session that was asked for is never established.
   */
  signInMode: "signin" | "register";
  openSignIn: () => void;
  openRegister: () => void;
  closeSignIn: () => void;
  isPanelOpen: boolean;
  togglePanel: () => void;
  closePanel: () => void;
  isAdminOpen: boolean;
  openAdmin: () => void;
  closeAdmin: () => void;
  /**
   * The code of a project the user just chose, waiting to be loaded.
   *
   * Held here rather than passed as a callback because the thing that can
   * load it - the app's own importer - is not a parent of the project
   * list, and these are siblings. Taken by the app and cleared at once, so
   * reopening the list later cannot re-load the last project.
   */
  pendingProject: Record<string, string> | null;
  setPendingProject: (code: Record<string, string>) => void;
  takePendingProject: () => Record<string, string> | null;
}

export const useAccountUi = create<AccountUiStore>((set, get) => ({
  isSignInOpen: false,
  signInMode: "signin",
  openSignIn: () => set({ isSignInOpen: true, signInMode: "signin", isPanelOpen: false }),
  openRegister: () =>
    set({ isSignInOpen: true, signInMode: "register", isPanelOpen: false }),
  closeSignIn: () => set({ isSignInOpen: false }),
  isPanelOpen: false,
  togglePanel: () => set((state) => ({ isPanelOpen: !state.isPanelOpen })),
  closePanel: () => set({ isPanelOpen: false }),
  isAdminOpen: false,
  openAdmin: () => set({ isAdminOpen: true, isPanelOpen: false }),
  closeAdmin: () => set({ isAdminOpen: false }),
  pendingProject: null,
  setPendingProject: (code) => set({ pendingProject: code }),
  takePendingProject: () => {
    const { pendingProject } = get();
    // Taken and cleared in one step: leaving it here would load the same
    // project again the next time anything read this store.
    set({ pendingProject: null });
    return pendingProject;
  },
}));
