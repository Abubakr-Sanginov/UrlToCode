import { useEffect } from "react";
import { ensureAccountLoaded, useAccountStore } from "../store/account-store";
import { tokenForSocket } from "../lib/accounts";

/**
 * Who is signed in, and what is left of the day's allowance.
 *
 * A view over one store, so every part of the app reads the same answer.
 */
export function useAccount() {
  const account = useAccountStore((state) => state.account);
  const usage = useAccountStore((state) => state.usage);
  const loading = useAccountStore((state) => state.loading);
  const error = useAccountStore((state) => state.error);
  const refresh = useAccountStore((state) => state.refresh);
  const signIn = useAccountStore((state) => state.signIn);
  const signOut = useAccountStore((state) => state.signOut);
  const resetError = useAccountStore((state) => state.resetError);

  // The first thing on screen asks; everyone after that reads what it found.
  useEffect(() => {
    ensureAccountLoaded();
  }, []);

  return {
    account,
    usage,
    loading,
    error,
    refresh,
    signIn,
    signOut,
    resetError,
    signedIn: account !== null,
    canRun: account !== null,
    token: tokenForSocket(),
  };
}
