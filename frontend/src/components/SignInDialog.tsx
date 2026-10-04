import { useEffect, useState } from "react";
import { FaGithub } from "react-icons/fa";
import { FcGoogle } from "react-icons/fc";
import { useAccount } from "../hooks/useAccount";
import { HTTP_BACKEND_URL } from "../config";
import { listProviders } from "../lib/oauth";

const HINT = "mt-1 text-xs text-muted-foreground";

const NAMES: Record<string, string> = {
  google: "Google",
  github: "GitHub",
};

/**
 * Signing in, and what the account gets.
 *
 * Only through Google and GitHub. There is no password form to fall back on,
 * which is the point: a password is something people reuse, reuse again, and
 * forget, and a free account that can run one clone a day is not worth
 * storing a credential for.
 *
 * Nothing calls back into this page when it succeeds. The round trip leaves
 * for the provider and comes back through a redirect, so the app reloads and
 * reads the session for itself.
 *
 * The limits are shown before signing up, not discovered afterwards: a free
 * account is worth signing up for, and worth walking away from if that was
 * not the intent.
 */
export function SignInDialog() {
  const { usage } = useAccount();
  const [providers, setProviders] = useState<string[]>([]);
  // Why there are no buttons, when there are none. Two causes that look
  // identical from outside and need opposite fixes: a server with no keys
  // set, and a request that never arrived. Only the second is worth telling
  // somebody about in detail, and it needs to be visible rather than hidden
  // behind a polite message that could mean either.
  const [problem, setProblem] = useState<{
    kind: "not-configured" | "unreachable";
    detail: string;
  } | null>(null);

  // An error the provider sent back with: the round trip leaves the page,
  // so there is nothing left in memory to show it from.
  const [returned, setReturned] = useState("");

  useEffect(() => {
    void listProviders().then((result) => {
      if (result.kind === "ready") {
        setProviders(result.providers);
        return;
      }
      setProblem({
        kind: result.kind,
        detail:
          result.kind === "unreachable"
            ? result.detail
            : "the server has no provider keys set",
      });
    });
    const fromUrl = new URLSearchParams(window.location.search).get("signInError");
    if (fromUrl) {
      setReturned(fromUrl);
      // Taken off the address so a reload does not keep saying it, and so
      // the message is not left sitting in the bar to be copied around.
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-sm rounded-xl border border-border bg-background p-5 shadow-xl">
        <h2 className="text-sm font-medium text-foreground">Sign in</h2>
        <p className={HINT}>
          A free account keeps {usage?.maxProjects ?? 1} project and runs{" "}
          {usage?.remaining ?? 1} clone a day. No card.
        </p>

        {returned && (
          <p role="alert" className="mt-3 text-xs text-red-500">
            {returned}
          </p>
        )}

        {problem ? (
          <div className="mt-4 rounded-lg border border-border p-3 text-xs leading-5 text-muted-foreground">
            <p>
              {problem.kind === "not-configured"
                ? "Signing in has not been set up on this server yet. Come back later."
                : "Could not reach the server to ask which providers are available."}
            </p>
            {/* The detail is what turns this from a dead end into a
                diagnosis: it names the URL that failed or the status that
                came back, which is the difference between "something is
                wrong" and "this browser is running last month's build". */}
            <p className="mt-1 break-all font-mono text-[10px] opacity-70">
              {problem.detail}
            </p>
          </div>
        ) : (
          <div className="mt-4 space-y-2">
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
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
              >
                {provider === "github" ? (
                  <FaGithub aria-hidden="true" />
                ) : (
                  <FcGoogle aria-hidden="true" />
                )}
                Continue with {NAMES[provider] ?? provider}
              </a>
            ))}
          </div>
        )}

        {usage && usage.tier === "free" && (
          <p className={HINT}>
            {usage.maxProjects} project · {usage.remaining} run
            {usage.remaining === 1 ? "" : "s"} left today
          </p>
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