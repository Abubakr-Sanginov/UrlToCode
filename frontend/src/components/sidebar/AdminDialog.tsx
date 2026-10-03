import { useCallback, useEffect, useState } from "react";
import {
  AdminAccount,
  adminToken,
  fetchAccounts,
  forgetAdminToken,
  rememberAdminToken,
  resetLimits,
  saveLimits,
} from "../../lib/admin";

const INPUT =
  "mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm";
const LABEL = "text-xs font-medium text-muted-foreground";

/**
 * Raising someone's limit, and only that.
 *
 * The box shows what the account has now, not what its plan would give
 * it. Leaving a box alone keeps the plan; typing a number overrides it.
 * That distinction is the whole screen, so it is stated in the UI rather
 * than left to be inferred from the result.
 */
export function AdminDialog({ onClose }: { onClose: () => void }) {
  const [token, setToken] = useState(adminToken());
  const [draft, setDraft] = useState(token);
  const [accounts, setAccounts] = useState<AdminAccount[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");

  const load = useCallback(async (withToken: string) => {
    setBusy(true);
    setError("");
    try {
      const result = await fetchAccounts(withToken);
      setAccounts(result.accounts);
    } catch (cause) {
      setAccounts([]);
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (token) void load(token);
    // Only on open: re-reading the token would fight the field while it is
    // being typed into.
  }, [load, token]);

  const shown = accounts.filter((account) =>
    account.email.toLowerCase().includes(filter.trim().toLowerCase())
  );

  return (
    <div
      className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60 p-4"
      data-testid="admin-dialog"
    >
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-border bg-background shadow-2xl">
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h2 className="text-sm font-medium text-foreground">Accounts</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            Close
          </button>
        </div>

        <div className="border-b border-border px-5 py-3">
          <label className={LABEL} htmlFor="admin-token">
            Admin token
          </label>
          <div className="flex gap-2">
            <input
              id="admin-token"
              type="password"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder="ADMIN_TOKEN from the server environment"
              className={INPUT}
            />
            <button
              type="button"
              onClick={() => {
                rememberAdminToken(draft);
                setToken(draft);
              }}
              className="shrink-0 rounded-lg bg-brand px-3 py-2 text-sm font-medium text-brand-foreground"
            >
              Open
            </button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Kept for this tab only. Set <code>ADMIN_TOKEN</code> in{" "}
            <code>backend/.env</code>.
          </p>
        </div>

        {error && (
          <p className="border-b border-border bg-destructive/10 px-5 py-2 text-sm text-destructive">
            {error}
          </p>
        )}

        {accounts.length > 0 && (
          <div className="border-b border-border px-5 py-2">
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="Find by email"
              className={INPUT}
            />
          </div>
        )}

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3">
          {busy && <p className="text-sm text-muted-foreground">Loading…</p>}
          {!busy && accounts.length === 0 && !error && (
            <p className="text-sm text-muted-foreground">
              Enter the admin token to list accounts.
            </p>
          )}
          {shown.map((account) => (
            <AccountRow
              key={account.id}
              account={account}
              token={token}
              onSaved={(updated) =>
                setAccounts((list) =>
                  list.map((item) => (item.id === updated.id ? updated : item))
                )
              }
            />
          ))}
        </div>

        {accounts.length > 0 && (
          <div className="border-t border-border px-5 py-2 text-right">
            <button
              type="button"
              onClick={() => {
                forgetAdminToken();
                setToken("");
                setAccounts([]);
              }}
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              Forget the token
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function AccountRow({
  account,
  token,
  onSaved,
}: {
  account: AdminAccount;
  token: string;
  onSaved: (account: AdminAccount) => void;
}) {
  // Seeded from what the account has now, and left alone until typed into,
  // so an untouched row never quietly pins a tier's number in place.
  const [daily, setDaily] = useState(String(account.dailyActions));
  const [projects, setProjects] = useState(String(account.maxProjects));
  const [note, setNote] = useState(account.note);
  const [error, setError] = useState("");

  const unchanged =
    daily === String(account.dailyActions) &&
    projects === String(account.maxProjects) &&
    note === account.note;

  async function save() {
    setError("");
    try {
      onSaved(
        await saveLimits(token, account.id, {
          dailyActions: Number(daily),
          maxProjects: Number(projects),
          note,
        })
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    }
  }

  async function back_to_tier() {
    setError("");
    try {
      const updated = await resetLimits(token, account.id);
      setDaily(String(updated.dailyActions));
      setProjects(String(updated.maxProjects));
      setNote(updated.note);
      onSaved(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    }
  }

  const overridden = account.overrideDailyActions !== null;

  return (
    <div className="border-b border-border py-3 last:border-b-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm font-medium text-foreground">{account.email}</p>
        <p className="text-xs text-muted-foreground">
          {account.tier} · today {account.usedGenerate} generated,{" "}
          {account.usedEdit} edited · {account.projects} projects
        </p>
      </div>

      <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div>
          <label className={LABEL} htmlFor={`daily-${account.id}`}>
            Runs a day
          </label>
          <input
            id={`daily-${account.id}`}
            type="number"
            min={0}
            value={daily}
            onChange={(event) => setDaily(event.target.value)}
            className={INPUT}
          />
        </div>
        <div>
          <label className={LABEL} htmlFor={`projects-${account.id}`}>
            Projects
          </label>
          <input
            id={`projects-${account.id}`}
            type="number"
            min={0}
            value={projects}
            onChange={(event) => setProjects(event.target.value)}
            className={INPUT}
          />
        </div>
        <div>
          <label className={LABEL} htmlFor={`note-${account.id}`}>
            Note
          </label>
          <input
            id={`note-${account.id}`}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder="optional"
            className={INPUT}
          />
        </div>
      </div>

      <div className="mt-2 flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={unchanged}
          className="rounded-lg border border-border px-3 py-1 text-xs font-medium text-foreground transition-colors hover:bg-accent disabled:opacity-40"
        >
          Save
        </button>
        {overridden && (
          <button
            type="button"
            onClick={back_to_tier}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            Back to {account.tier} limits
          </button>
        )}
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    </div>
  );
}
