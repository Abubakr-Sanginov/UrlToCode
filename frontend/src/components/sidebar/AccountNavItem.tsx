import { useAccount } from "../../hooks/useAccount";
import { useAccountUi } from "../../store/account-ui-store";
import { AccountPanel } from "./AccountPanel";
import { LuUser } from "react-icons/lu";

/**
 * Who is signed in, on the icon strip.
 *
 * The question this answers is "am I in my account or not", and it has to
 * be answerable without clicking anything - a clone is refused to anyone
 * who is not, so guessing wrong costs a run. Signed out it reads "Sign
 * in"; signed in it shows the account's first letter and what is left of
 * the day's allowance.
 */
export function AccountNavItem() {
  const { account, usage, loading } = useAccount();
  const togglePanel = useAccountUi((state) => state.togglePanel);

  if (loading) {
    return <div className="h-[42px]" aria-hidden="true" />;
  }

  if (!account) {
    return (
      <button
        type="button"
        onClick={togglePanel}
        aria-label="Account"
        className="relative flex items-center justify-center gap-1 rounded-md p-2 text-muted-foreground transition-colors hover:bg-accent/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:flex-col lg:gap-1 lg:px-2 lg:py-1.5"
        title="Sign in or open the admin screen"
        data-testid="account-nav"
      >
        <LuUser className="h-[18px] w-[18px]" aria-hidden="true" />
        <span className="hidden font-mono text-[10px] leading-none lg:block">
          Sign in
        </span>
      </button>
    );
  }

  const initial = (account.email.charAt(0) || "A").toUpperCase();
  const left = usage?.remaining;
  const label = left === undefined ? "Account" : `${left} left`;

  return (
    <button
      type="button"
      onClick={togglePanel}
      aria-label={`Account: ${account.email}`}
      className="relative flex items-center justify-center gap-1 rounded-md p-2 text-foreground transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:flex-col lg:gap-1 lg:px-2 lg:py-1.5"
      title={`${account.email} - ${usage?.tier ?? ""} plan`}
      data-testid="account-nav"
    >
      <span
        className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-brand text-[10px] font-semibold text-brand-foreground"
        aria-hidden="true"
      >
        {initial}
      </span>
      <span className="hidden font-mono text-[10px] leading-none lg:block">
        {label}
      </span>
      {/* A second badge rather than a colour change: the letter alone
          says who, not that there is an account at all. */}
      {left === 0 && (
        <span
          className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-warning lg:right-2 lg:top-1.5"
          aria-hidden="true"
        />
      )}
    </button>
  );
}

export { AccountPanel };
