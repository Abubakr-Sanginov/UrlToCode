import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { LuArrowLeft, LuFolder, LuTrash2 } from "react-icons/lu";
import {
  Project,
  deleteProject,
  listProjects,
  openProject,
} from "../lib/accounts";
import { GENERATED_FILE_PREFIX } from "../lib/projectFiles";
import { adminToken } from "../lib/admin";
import { PlansPanel } from "../components/sidebar/PlansPanel";
import { useAccount } from "../hooks/useAccount";
import { usePageTheme } from "../hooks/usePageTheme";
import { useAccountStore } from "../store/account-store";
import { useAccountUi } from "../store/account-ui-store";
import { useCloneStore } from "../store/clone-store";

function formatDate(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/**
 * The account's own page at /profile.
 *
 * Who is signed in, which plan, what is left of today's allowance, the
 * projects kept, and the way out. The icon strip's popover answers "what have
 * I got left" at a glance; this is where the rest of the account lives.
 */
export default function ProfilePage() {
  usePageTheme();
  const navigate = useNavigate();
  const { account, usage, signOut } = useAccount();
  const setPendingProject = useAccountUi((state) => state.setPendingProject);

  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [opening, setOpening] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const result = await listProjects();
      setProjects(result.projects);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function open(runId: string) {
    setOpening(runId);
    setError("");
    try {
      const project = await openProject(runId);
      const generatedFiles: Record<string, string> = {};
      const code: Record<string, string> = {};
      for (const [key, content] of Object.entries(project.code)) {
        if (key.startsWith(GENERATED_FILE_PREFIX)) {
          generatedFiles[key.slice(GENERATED_FILE_PREFIX.length)] = content;
        } else {
          code[key] = content;
        }
      }
      useCloneStore.getState().setCloneSource({
        baseUrl: project.baseUrl,
        runId: project.runId,
        generatedFiles,
      });
      // Same hand-over the projects page uses: the app takes the code as it
      // mounts and loads it into the editor.
      setPendingProject(code);
      navigate("/");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
      setOpening(null);
    }
  }

  async function remove(runId: string) {
    setBusy(true);
    try {
      const result = await deleteProject(runId);
      setProjects(result.projects);
      void useAccountStore.getState().refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOut() {
    await signOut();
    navigate("/welcome", { replace: true });
  }

  const initial = account?.email.charAt(0).toUpperCase() ?? "?";

  return (
    <div className="min-h-dvh bg-canvas text-foreground">
      <header className="mx-auto flex w-full max-w-3xl items-center justify-between px-5 py-5">
        <Link
          to="/"
          className="inline-flex items-center gap-1.5 text-sm text-foreground-muted transition-colors hover:text-foreground"
        >
          <LuArrowLeft className="h-4 w-4" aria-hidden="true" />
          Back to the app
        </Link>
        <Link to="/welcome" className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-brand font-mono text-xs font-bold text-brand-foreground">
            &gt;_
          </span>
          <span className="text-sm font-semibold tracking-tight">UrltoCode</span>
        </Link>
      </header>

      <main className="mx-auto w-full max-w-3xl px-5 pb-20">
        <h1 className="mt-4 text-3xl font-semibold tracking-tight">Profile</h1>

        <section
          className="mt-8 flex items-center gap-4 rounded-xl border border-border bg-card p-5"
          aria-label="Account"
        >
          <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-brand text-xl font-semibold text-brand-foreground">
            {initial}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-base font-medium">{account?.email}</p>
            {usage && (
              <p className="mt-0.5 text-sm capitalize text-foreground-muted">
                {usage.tier} plan
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => void handleSignOut()}
            className="shrink-0 rounded-lg border border-border px-4 py-2 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Sign out
          </button>
        </section>

        {usage && (
          <section className="mt-4 grid gap-4 sm:grid-cols-2" aria-label="Usage">
            <div className="rounded-xl border border-border bg-card p-5">
              <p className="text-xs uppercase tracking-wider text-muted-foreground">
                Runs left today
              </p>
              <p
                className={`mt-2 text-3xl font-semibold tabular-nums ${
                  usage.remaining === 0 ? "text-warning" : ""
                }`}
              >
                {usage.remaining}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {usage.used} used · resets{" "}
                {new Date(usage.resetsAt * 1000).toLocaleTimeString(undefined, {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </p>
            </div>
            <div className="rounded-xl border border-border bg-card p-5">
              <p className="text-xs uppercase tracking-wider text-muted-foreground">
                Projects
              </p>
              <p className="mt-2 text-3xl font-semibold tabular-nums">
                {usage.projects}
                <span className="text-lg text-muted-foreground">
                  {" "}
                  / {usage.maxProjects}
                </span>
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Kept on your account
              </p>
            </div>
          </section>
        )}

        {usage?.tier === "free" && (
          <section className="mt-4 rounded-xl border border-border bg-card p-5" aria-label="Plans">
            <PlansPanel currentTier="free" />
          </section>
        )}

        <section className="mt-10" aria-label="Projects">
          <h2 className="text-lg font-semibold tracking-tight">Your projects</h2>

          {error && (
            <div
              role="alert"
              className="mt-4 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
            >
              {error}
            </div>
          )}

          {busy && projects.length === 0 && (
            <p className="mt-6 text-sm text-muted-foreground">Loading…</p>
          )}

          {!busy && projects.length === 0 && !error && (
            <div className="mt-4 flex flex-col items-center gap-3 rounded-xl border border-border bg-card px-6 py-10 text-center">
              <LuFolder className="h-7 w-7 text-muted-foreground" aria-hidden="true" />
              <p className="text-sm text-muted-foreground">
                Nothing here yet. Clone a site and it will be kept for you.
              </p>
              <Link
                to="/"
                className="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
              >
                Clone a site
              </Link>
            </div>
          )}

          {projects.length > 0 && (
            <ul className="mt-4 divide-y divide-border rounded-xl border border-border bg-card">
              {projects.map((project) => (
                <li
                  key={project.runId}
                  className="flex items-center justify-between gap-4 px-4 py-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{project.name}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {project.sourceUrl} · saved {formatDate(project.savedAt * 1000)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => void open(project.runId)}
                      disabled={opening !== null}
                      className="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-brand-foreground disabled:opacity-60"
                    >
                      {opening === project.runId ? "Opening…" : "Open"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void remove(project.runId)}
                      aria-label={`Delete ${project.name}`}
                      disabled={busy}
                      className="rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-destructive disabled:opacity-60"
                    >
                      <LuTrash2 className="h-4 w-4" aria-hidden="true" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Only for whoever already holds an operator token: the admin
            screen opens from the app's address marker. */}
        {adminToken().length > 0 && (
          <p className="mt-10 text-center">
            <a
              href="/#admin"
              className="text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              Admin
            </a>
          </p>
        )}
      </main>
    </div>
  );
}
