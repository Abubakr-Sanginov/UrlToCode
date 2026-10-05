import { useEffect, useState } from "react";
import { LuStar } from "react-icons/lu";
import { starBalance, type StarBalance } from "../../lib/telegram";

/**
 * The bot's Stars balance, for whoever owns the bot.
 *
 * Shown to nobody else, and not by hiding itself: on mount it asks the
 * server, and the server answers from the Telegram id it verified rather
 * than from anything on the page. A button that is merely not drawn is not
 * something anybody can be stopped from pressing, so the decision is made
 * where it cannot be edited.
 *
 * The number cannot be withdrawn from here even by the owner - Telegram has
 * no method for it, and the withdrawal is done in Fragment. The button says
 * so rather than letting the balance read as money in hand.
 */
export function StarBalanceButton() {
  const [balance, setBalance] = useState<StarBalance | null>(null);
  const [open, setOpen] = useState(false);
  const [asked, setAsked] = useState(false);

  useEffect(() => {
    let alive = true;
    void starBalance().then((found) => {
      if (!alive) return;
      setBalance(found);
      setAsked(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  // Stays hidden until the server has said yes. Rendering it first and
  // hiding it afterwards would flash at people who are not the owner.
  if (!asked || !balance) return null;

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        title="Stars the bot has been paid"
        className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex items-center gap-2 text-sm">
          <LuStar className="h-4 w-4 text-brand" aria-hidden="true" />
          <span className="text-foreground-muted">Stars</span>
        </span>
        <span className="font-mono text-sm tabular-nums">{balance.balance}</span>
      </button>

      {open && (
        <div className="mt-2 rounded-lg border border-border bg-card p-3 text-sm">
          <dl className="space-y-1.5">
            <div className="flex justify-between gap-4">
              <dt className="text-foreground-muted">Balance</dt>
              <dd className="font-mono tabular-nums">{balance.balance}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-foreground-muted">Paid in total</dt>
              <dd className="font-mono tabular-nums">{balance.paidIn}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-foreground-muted">Payments</dt>
              <dd className="font-mono tabular-nums">{balance.transactions}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            Telegram has no way to withdraw these from the bot. Take them out
            in Fragment, on the account that owns the bot, with a TON wallet
            connected.
          </p>
        </div>
      )}
    </div>
  );
}