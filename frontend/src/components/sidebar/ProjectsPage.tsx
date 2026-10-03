import { useCallback, useEffect, useState } from "react";
import { LuFolder, LuTrash2, LuArrowRight } from "react-icons/lu";
import {
  Project,
  deleteProject,
  listProjects,
  openProject,
} from "../../lib/accounts";
import { useAccountStore } from "../../store/account-store";
import { useAccountUi } from "../../store/account-ui-store";
import { useCloneStore } from "../../store/clone-store";
import { GENERATED_FILE_PREFIX } from "../../lib/projectFiles";

/**
 * The account's projects, as a page of their own.
 *
 * A page rather than a popover because it is where someone goes to find
 * something they made earlier, and a list that covers the screen disappears
 * at the first click anywhere else - which is the opposite of what coming
 * back here is for.
 *
 * Opening one does not clone the site again. It asks the server for the run
 * that made it, which is the whole reason a finished clone is recorded
 * against the account in the first place.
 */
export function ProjectsPage({
  onOpened,
  onGoToClone,
}: {
  onOpened: () => void;
  onGoToClone: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(true);
  const [opening, setOpening] = useState<string | null>(null);

  const setPendingProject = useAccountUi((state) => state.setPendingProject);

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
      // Handed over rather than loaded from here: this page is a sibling of
      // the app, not a child of it, and the app owns the code the editor
      // works from. It takes this as soon as the page is left.
      setPendingProject(code);
      onOpened();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setOpening(null);
    }
  }

  async function remove(runId: string) {
    setBusy(true);
    try {
      const result = await deleteProject(runId);
      setProjects(result.projects);
      // The panel counts projects; leaving it showing a number the list has
      // just contradicted is how the two start disagreeing in plain sight.
      void useAccountStore.getState().refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="relative flex flex-1 flex-col items-center overflow-y-auto px-4 py-10 sm:py-14">
      <div className="w-full max-w-2xl">
        <h1 className="text-center text-3xl font-semibold text-foreground sm:text-4xl">
          Projects
        </h1>
        <p className="mt-3 text-center text-foreground-muted">
          Everything you have cloned, ready to open again.
        </p>

        {error && (
          <div
            role="alert"
            className="mt-6 rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
          >
            {error}
          </div>
        )}

        {busy && projects.length === 0 && (
          <p className="mt-12 text-center text-sm text-muted-foreground">
            Loading…
          </p>
        )}

        {!busy && projects.length === 0 && !error && (
          <div className="mt-12 flex flex-col items-center gap-4 rounded-xl border border-border bg-card px-6 py-12 text-center">
            <LuFolder className="h-8 w-8 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm text-muted-foreground">
              Nothing here yet. Clone a site and it will be kept for you.
            </p>
            <button
              type="button"
              onClick={onGoToClone}
              className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-foreground"
            >
              Clone a site
              <LuArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        )}

        {projects.length > 0 && (
          <ul className="mt-8 divide-y divide-border rounded-xl border border-border bg-card">
            {projects.map((project) => (
              <li
                key={project.runId}
                className="flex items-center justify-between gap-4 px-4 py-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-foreground">
                    {project.name}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">
                    {project.sourceUrl}
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
      </div>
    </div>
  );
}