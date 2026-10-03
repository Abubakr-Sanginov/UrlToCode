import { useCallback, useEffect, useState } from "react";
import { LuCheck, LuCopy, LuLink, LuTrash2 } from "react-icons/lu";
import { Share, createShare, listShares, revokeShare, shareUrl } from "../../lib/shares";

/**
 * Making a link to send someone.
 *
 * Shows what has already been sent, because a link is a thing you cannot
 * take back quietly: once it is in a chat, only revoking it helps, and the
 * only way to know which one to revoke is to see the list.
 */
export function ShareDialog({
  runId,
  pagePath = "/",
  onClose,
}: {
  runId: string;
  pagePath?: string;
  onClose: () => void;
}) {
  const [shares, setShares] = useState<Share[]>([]);
  const [limit, setLimit] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await listShares();
      setShares(result.shares);
      setLimit(result.usage.limit);
      setRemaining(result.usage.remaining);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function share() {
    setBusy(true);
    setError("");
    try {
      const made = await createShare(runId, pagePath);
      setRemaining(made.usage.remaining);
      await load();
      // Copied the moment it exists: the point of the button is that the
      // link ends up in a chat, and making someone hunt for a text field
      // to copy is how links get lost.
      await copy(shareUrl(made.token));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
      setTimeout(() => setCopied(null), 2000);
    } catch {
      // Clipboard access is refused in more places than it works. Say so
      // rather than appearing to have copied something.
      setError("Your browser would not let the page copy. Copy the link by hand.");
    }
  }

  async function revoke(token: string) {
    setBusy(true);
    try {
      await revokeShare(token);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/50 p-4">
      <div className="w-full max-w-md rounded-xl border border-border bg-background p-5 shadow-xl">
        <h2 className="text-sm font-medium text-foreground">Share this site</h2>
        <p className="mt-1 text-xs leading-5 text-muted-foreground">
          A link that opens the site itself, not the code. Anyone with it can
          look, without an account.
        </p>

        {error && (
          <p role="alert" className="mt-3 text-xs text-red-500">
            {error}
          </p>
        )}

        {limit === 0 && !error && (
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            Sharing is part of the paid plans. The free one keeps your site to
            yourself.
          </p>
        )}

        {limit > 0 && (
          <>
            <button
              type="button"
              onClick={() => void share()}
              disabled={busy || remaining === 0}
              data-testid="share-create"
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-foreground disabled:opacity-60"
            >
              <LuLink className="h-4 w-4" aria-hidden="true" />
              {remaining === 0
                ? "No links left this month"
                : `Make a link (${remaining} left)`}
            </button>

            {shares.length > 0 && (
              <ul className="mt-4 divide-y divide-border rounded-lg border border-border">
                {shares.map((made) => {
                  const url = shareUrl(made.token);
                  return (
                    <li
                      key={made.token}
                      className="flex items-center gap-2 px-3 py-2"
                    >
                      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                        {url.replace(/^https?:\/\//, "")}
                      </span>
                      <button
                        type="button"
                        onClick={() => void copy(url)}
                        aria-label="Copy link"
                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
                      >
                        {copied === url ? (
                          <LuCheck className="h-3.5 w-3.5 text-green-600" aria-hidden="true" />
                        ) : (
                          <LuCopy className="h-3.5 w-3.5" aria-hidden="true" />
                        )}
                      </button>
                      <button
                        type="button"
                        onClick={() => void revoke(made.token)}
                        aria-label="Stop sharing"
                        disabled={busy}
                        className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-destructive disabled:opacity-60"
                      >
                        <LuTrash2 className="h-3.5 w-3.5" aria-hidden="true" />
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        )}

        <button
          type="button"
          onClick={onClose}
          className="mt-4 w-full rounded-lg border border-border px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
        >
          Close
        </button>
      </div>
    </div>
  );
}