import { useAccount } from "../../hooks/useAccount";
import { useAccountUi } from "../../store/account-ui-store";
import { adminToken } from "../../lib/admin";

/**
 * The account behind the icon strip's avatar: the address, the plan, what
 * is left today, and the way out.
 *
 * Deliberately not a settings tab. The question a free-tier user has is
 * "what have I got left", and that is a glance, not a configuration.
 */
export function AccountPanel() {
  const { account, usage, signOut } = useAccount();
  const openAdmin = useAccountUi((state) => state.openAdmin);
  const openSignIn = useAccountUi((state) => state.openSignIn);
  const openRegister = useAccountUi((state) => state.openRegister);
  const isOperator = adminToken().length > 0;

  return (
    <div
      className="w-[17rem] rounded-lg border border-border bg-card p-3 text-left shadow-xl"
      data-testid="account-panel"
    >
      {account ? (
        <>
          <p className="truncate text-sm font-medium text-foreground">
            {account.email}
          </p>

          {usage && (
            <>
              <p className="mt-0.5 text-xs capitalize text-muted-foreground">
                {usage.tier} plan
              </p>
              <dl className="mt-3 space-y-1.5 text-xs">
                <div className="flex items-center justify-between">
                  <dt className="text-muted-foreground">Runs left today</dt>
                  <dd
                    className={`font-medium tabular-nums ${
                      usage.remaining === 0 ? "text-warning" : "text-foreground"
                    }`}
                  >
                    {usage.remaining}
                  </dd>
                </div>
                <div className="flex items-center justify-between">
                  <dt className="text-muted-foreground">Projects</dt>
                  <dd className="font-medium tabular-nums text-foreground">
                    {usage.projects} of {usage.maxProjects}
                  </dd>
                </div>
              </dl>
              {usage.remaining === 0 && (
                <p className="mt-2 text-xs leading-5 text-muted-foreground">
                  Today&apos;s runs are used up. They come back at midnight.
                </p>
              )}
            </>
          )}

          <button
            type="button"
            onClick={() => void signOut()}
            className="mt-3 w-full rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Sign out
          </button>
        </>
      ) : (
        <>
          <p className="text-sm font-medium text-foreground">Not signed in</p>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
            One free project, one clone a day.
          </p>
          <button
            type="button"
            onClick={openSignIn}
            className="mt-3 w-full rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-brand-foreground"
          >
            Sign in
          </button>
          {/* Someone arriving here for the first time has no account to sign
              into, and "Sign in" alone leaves them with nothing to click. */}
          <button
            type="button"
            onClick={openRegister}
            className="mt-2 w-full rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-accent"
          >
            Create an account
          </button>
        </>
      )}

      {/* Shown only to whoever already holds an operator token. A link to
          the admin screen on every account's panel would tell every user
          it exists; the route stays reachable by hand for the one person
          who typed the token in. */}
      {isOperator && (
        <button
          type="button"
          onClick={openAdmin}
          className="mt-2 w-full rounded-lg px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          data-testid="open-admin"
        >
          Admin
        </button>
      )}
    </div>
  );
}
