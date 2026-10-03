import { useCallback, useEffect, useState } from "react";
import toast from "react-hot-toast";
import { LuExternalLink, LuPackage, LuPlay, LuSquare, LuTrash2, LuX } from "react-icons/lu";
import { Button } from "../ui/button";
import { HTTP_BACKEND_URL } from "../../config";
import { useCloneStore } from "../../store/clone-store";
import {
  DevServerInfo,
  SavedClone,
  describeProjectSize,
  downloadDeployBundle,
  fetchCloneLibrary,
  forgetClone,
  startDevServer,
  stopDevServer,
} from "../../lib/localProject";

/**
 * The clones this backend has already saved.
 *
 * A generated site outlives the tab that made it. Without a list here the
 * only way back to yesterday's clone is to remember which backend it is on
 * and guess at the URL, and the run is prunable in the meantime.
 *
 * Opening one shows it as the backend serves it. There is no "load into the
 * editor" here, and that is deliberate: the saved copy is the artefact the
 * user took away, and reopening it for editing would fork it from what was
 * actually deployed.
 */
export function CloneLibrary({ onClose }: { onClose: () => void }) {
  const [clones, setClones] = useState<SavedClone[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  // The project whose dev server is running, and the URL it answers on.
  const [dev, setDev] = useState<DevServerInfo | null>(null);
  const [devBusy, setDevBusy] = useState(false);
  const setDevServerRunId = useCloneStore((s) => s.setDevServerRunId);

  const load = useCallback(async () => {
    try {
      setClones(await fetchCloneLibrary());
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The library could not be read.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Starting a dev server runs model-generated code on this machine, so it
  // is never done without a press, and a failure says why in the user's
  // own words rather than as a failed request.
  const toggleDev = async (clone: SavedClone) => {
    if (devBusy) return;
    setDevBusy(true);
    try {
      if (dev?.runId === clone.runId) {
        await stopDevServer(clone.runId);
        setDev(null);
        setDevServerRunId(null);
        return;
      }
      const started = await startDevServer(clone.runId);
      setDev(started.running ? started : null);
      setDevServerRunId(started.running ? started.runId : null);
      if (!started.running) toast.error(started.error || "The dev server did not start.");
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "The dev server did not start.", {
        duration: 8000,
      });
    } finally {
      setDevBusy(false);
    }
  };

  const forget = async (clone: SavedClone) => {
    if (isBusy) return;
    setIsBusy(true);
    try {
      await forgetClone(clone.runId);
      setClones((previous) => (previous ?? []).filter((c) => c.runId !== clone.runId));
      toast.success(`Removed "${clone.name}"`);
    } catch (cause) {
      toast.error(cause instanceof Error ? cause.message : "Could not remove it.", {
        duration: 6000,
      });
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div
      className="absolute inset-x-2 bottom-2 z-20 max-h-[70%] overflow-hidden rounded-2xl border border-border bg-card shadow-modal"
      data-testid="clone-library"
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h2 className="text-sm font-medium text-foreground">Saved clones</h2>
          <p className="text-xs text-muted-foreground">
            Every project this backend has written to disk
          </p>
        </div>
        <Button
          onClick={onClose}
          variant="ghost"
          size="icon"
          aria-label="Close the clone library"
          className="h-7 w-7 text-muted-foreground hover:text-foreground"
        >
          <LuX className="h-3.5 w-3.5" aria-hidden="true" />
        </Button>
      </div>

      <div className="max-h-56 overflow-y-auto p-2">
        {error && <p className="px-2 py-3 text-xs text-destructive">{error}</p>}
        {!error && clones === null && (
          <p className="px-2 py-3 text-xs text-muted-foreground">Loading...</p>
        )}
        {!error && clones !== null && clones.length === 0 && (
          <p className="px-2 py-3 text-xs text-muted-foreground">
            Nothing saved yet. Save a clone and it will be here.
          </p>
        )}
        {clones?.map((clone) => (
          <div
            key={clone.runId}
            className="group flex items-center gap-2 rounded-lg px-2 py-2 transition-colors hover:bg-accent"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-foreground">{clone.name}</p>
              <p className="truncate text-[11px] text-muted-foreground">
                {clone.sourceUrl || `${clone.runId} · `}
                {clone.pageCount} page{clone.pageCount === 1 ? "" : "s"}
                {clone.hasServer ? " · with API" : ""} ·{" "}
                {describeProjectSize(clone.sizeBytes)}
              </p>
            </div>
            <a
              href={
                dev?.runId === clone.runId ? dev.url : `${HTTP_BACKEND_URL}${clone.url}`
              }
              target="_blank"
              rel="noopener noreferrer"
              title="Open this clone"
              aria-label={`Open ${clone.name}`}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              data-testid={`open-clone-${clone.runId}`}
            >
              <LuExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
            </a>
            <button
              type="button"
              onClick={() => void toggleDev(clone)}
              disabled={devBusy}
              title={
                dev?.runId === clone.runId
                  ? "Stop the dev server"
                  : "Run this project's own dev server with hot reloading"
              }
              aria-label={`Dev server for ${clone.name}`}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
              data-testid={`dev-clone-${clone.runId}`}
            >
              {dev?.runId === clone.runId ? (
                <LuSquare className="h-3.5 w-3.5 text-emerald-600" aria-hidden="true" />
              ) : (
                <LuPlay className="h-3.5 w-3.5" aria-hidden="true" />
              )}
            </button>
            <button
              type="button"
              onClick={() => downloadDeployBundle(clone.runId, clone.name)}
              title="Download with the files its host needs"
              aria-label={`Download ${clone.name} for deploying`}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              data-testid={`deploy-clone-${clone.runId}`}
            >
              <LuPackage className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => void forget(clone)}
              disabled={isBusy}
              title="Delete this clone from disk"
              aria-label={`Remove ${clone.name}`}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
              data-testid={`forget-clone-${clone.runId}`}
            >
              <LuTrash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>

      {dev && (
        <div
          className="flex items-center gap-2 border-t border-border px-4 py-2 text-[11px] text-muted-foreground"
          data-testid="dev-server-status"
        >
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" aria-hidden="true" />
          <span className="min-w-0 flex-1 truncate">
            Dev server running on {dev.url} — edits you make reload themselves
          </span>
          <a
            href={dev.url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 text-brand underline-offset-2 hover:underline"
          >
            Open
          </a>
        </div>
      )}
    </div>
  );
}
