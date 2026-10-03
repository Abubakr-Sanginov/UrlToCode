import { useEffect, useState } from "react";
import { FaGithub } from "react-icons/fa";
import { FcGoogle } from "react-icons/fc";
import { useAccount } from "../hooks/useAccount";
import { useAccountUi } from "../store/account-ui-store";
import { HTTP_BACKEND_URL } from "../config";
import { listProviders } from "../lib/oauth";

const LABEL = "text-sm font-medium text-foreground";
const HINT = "mt-1 text-xs text-muted-foreground";
const INPUT =
  "mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm";

/**
 * Signing in, and what the account gets.
 *
 * The limits are shown before signing up, not discovered afterwards: a
 * free account that can keep one project and run one clone a day is worth
 * signing up for, and worth walking away from if that was not the intent.
 */
export function SignInDialog({ onSignedIn }: { onSignedIn?: () => void }) {
  const { signIn, usage, loading, error } = useAccount();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // Which form to open on is the caller's decision, not the dialog's: the
  // button that opened it is labelled "Sign in", so the form has to be the
  // one that signs in. The dialog is remounted each time it opens, so this
  // reads the mode fresh rather than keeping the last one.
  const [isNew, setIsNew] = useState(
    useAccountUi.getState().signInMode === "register"
  );
  const [providers, setProviders] = useState<string[]>([]);

  // An error the provider sent back with: the round trip leaves the page,
  // so there is nothing left in memory to show it from.
  const [returned, setReturned] = useState("");

  useEffect(() => {
    void listProviders().then(setProviders);
    const fromUrl = new URLSearchParams(window.location.search).get("signInError");
    if (fromUrl) {
      setReturned(fromUrl);
      // Taken off the address so a reload does not keep saying it, and so
      // the message is not left sitting in the bar to be copied around.
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const ok = await signIn(email, password, isNew);
    if (ok) {
      onSignedIn?.();
    }
  }

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-background p-5 shadow-xl">
        <h2 className="text-sm font-medium text-foreground">
          {isNew ? "Create an account" : "Sign in"}
        </h2>
        <p className={HINT}>
          {isNew
            ? "A free account keeps one project and runs one clone a day. No card."
            : "Welcome back."}
        </p>

        <form onSubmit={submit} className="mt-4 space-y-3">
          <div>
            <label htmlFor="account-email" className={LABEL}>
              Email
            </label>
            <input
              id="account-email"
              type="email"
              autoComplete="email"
              required
              className={INPUT}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div>
            <label htmlFor="account-password" className={LABEL}>
              Password
            </label>
            <input
              id="account-password"
              type="password"
              autoComplete={isNew ? "new-password" : "current-password"}
              required
              minLength={isNew ? 8 : undefined}
              className={INPUT}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {isNew && <p className={HINT}>At least 8 characters.</p>}
          </div>

          {(error || returned) && (
            <p role="alert" className="text-xs text-red-500">
              {error || returned}
            </p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white disabled:opacity-60"
          >
            {loading ? "Working..." : isNew ? "Create account" : "Sign in"}
          </button>
        </form>

        <button
          type="button"
          className="mt-3 text-xs text-muted-foreground underline"
          onClick={() => setIsNew((value) => !value)}
        >
          {isNew ? "I already have an account" : "Create an account instead"}
        </button>

        {usage && usage.tier === "free" && (
          <p className={HINT}>
            {usage.maxProjects} project · {usage.remaining} run
            {usage.remaining === 1 ? "" : "s"} left today
          </p>
        )}

        {providers.length > 0 && (
          <>
            <div className="my-3 flex items-center gap-2">
              <span className="h-px flex-1 bg-border" />
              <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
                or
              </span>
              <span className="h-px flex-1 bg-border" />
            </div>
            {providers.map((provider) => (
              <a
                key={provider}
                // A plain link, not a click handler. The provider's own
                // page has to be a real destination the browser can go to
                // and come back from - and because it leaves the page
                // entirely, the error cannot be handed over in memory, so
                // the server passes it back on the address.
                href={`${HTTP_BACKEND_URL}/api/auth/${provider}/start`}
                data-testid={`oauth-${provider}`}
                className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
              >
                {provider === "github" ? <FaGithub aria-hidden="true" /> : <FcGoogle aria-hidden="true" />}
                Continue with {provider === "github" ? "GitHub" : "Google"}
              </a>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

/**
 * The account strip: who is signed in and what is left.
 *
 * Always visible rather than hidden behind a menu, because the number is
 * the thing a user on a free tier needs before they press the button, not
 * after it is refused.
 */
export function AccountBadge({ onSignIn }: { onSignIn: () => void }) {
  const { account, usage, signOut } = useAccount();

  if (!account) {
    return (
      <button
        type="button"
        onClick={onSignIn}
        className="rounded-lg border border-border px-3 py-1.5 text-xs hover:bg-muted/60"
      >
        Sign in
      </button>
    );
  }

  return (
    <div className="flex items-center gap-3 rounded-lg border border-border px-3 py-1.5 text-xs">
      <span className="max-w-[12rem] truncate text-muted-foreground">
        {account.email}
      </span>
      {usage && (
        <span className="text-foreground">
          {usage.remaining} left · {usage.projects}/{usage.maxProjects} proj
        </span>
      )}
      <button
        type="button"
        onClick={() => void signOut()}
        className="text-muted-foreground underline"
      >
        Sign out
      </button>
    </div>
  );
}