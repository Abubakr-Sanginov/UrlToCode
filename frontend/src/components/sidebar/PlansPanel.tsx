import { useCallback, useEffect, useState } from "react";
import { LuCheck, LuSparkles } from "react-icons/lu";
import { TariffTier, buyPlan, planLinkInBot, tariff } from "../../lib/payments";
import { useAccountStore } from "../../store/account-store";
import { isInsideTelegram } from "../../lib/telegram";

const ALLOWANCES: Record<string, string> = {
  starter: "10 projects, 10 a day, 1 link a month",
  pro: "50 projects, 25 a day, 10 links a month",
  studio: "300 projects, 100 a day, 50 links a month",
};

/**
 * The plans, and the button that pays for one.
 *
 * Inside Telegram the button opens Telegram's own payment sheet. On the
 * website it opens the bot, which sends the invoice. Either way, nothing here
 * decides that a payment worked - the plan changes when the webhook says so, and the
 * account is re-read afterwards. A payment screen drawn by this app would be
 * one that could be edited.
 */
export function PlansPanel({ currentTier }: { currentTier: string }) {
  const [tiers, setTiers] = useState<TariffTier[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setTiers((await tariff()).tiers);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Paying happens in another app. Coming back to this tab is the moment to
  // ask the server whether the plan changed, rather than leaving the old one
  // on screen until a reload.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        void useAccountStore.getState().refresh();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  async function buy(tier: string) {
    setBusy(tier);
    setError("");
    try {
      if (isInsideTelegram()) {
        const opened = await buyPlan(tier);
        if (!opened) setError("This version of Telegram cannot open a payment.");
        return;
      }
      // A new tab, so this page is still here when the payment is done.
      const url = await planLinkInBot(tier);
      if (!window.open(url, "_blank", "noopener")) {
        // A blocked popup: leaving the page is better than doing nothing.
        window.location.assign(url);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center gap-2">
        <LuSparkles className="h-4 w-4 text-brand" aria-hidden="true" />
        <h2 className="text-sm font-medium text-foreground">Plans</h2>
      </div>

      {error && (
        <p role="alert" className="text-xs text-red-500">
          {error}
        </p>
      )}

      {tiers.map((tier) => {
        const isCurrent = tier.tier === currentTier;
        return (
          <div
            key={tier.tier}
            className="rounded-xl border border-border bg-card p-4"
          >
            <div className="flex items-baseline justify-between gap-2">
              <p className="text-sm font-medium capitalize text-foreground">
                {tier.tier}
              </p>
              <p className="text-sm font-medium text-foreground">
                {tier.stars} ⭐
              </p>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {ALLOWANCES[tier.tier] ?? "Everything in the plan below"}
            </p>
            <button
              type="button"
              onClick={() => void buy(tier.tier)}
              disabled={busy !== null || isCurrent}
              data-testid={`buy-${tier.tier}`}
              className="mt-3 w-full rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-foreground disabled:opacity-60"
            >
              {isCurrent ? (
                <>
                  <LuCheck className="mr-1 inline h-3.5 w-3.5" aria-hidden="true" />
                  Your plan
                </>
              ) : busy === tier.tier ? (
                "Opening…"
              ) : (
                `Pay ${tier.stars} Stars`
              )}
            </button>
          </div>
        );
      })}

      {!isInsideTelegram() && (
        <p className="text-xs leading-5 text-muted-foreground">
          Payment opens in our Telegram bot, where Stars are paid. Come back
          here afterwards and your plan will be updated.
        </p>
      )}
    </div>
  );
}