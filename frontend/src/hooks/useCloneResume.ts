import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { WS_BACKEND_URL } from "../config";
import {
  CloneRunError,
  CloneRunStatus,
  fetchCloneRunStatus,
  resumeCloneRun,
  stopCloneRun,
} from "../lib/cloneRuns";
import { Settings } from "../types";

/**
 * Picking a run up again after the page was closed.
 *
 * A clone is minutes of model calls, and a browser tab that goes away used to
 * take them with it. The work now belongs to the run rather than to the
 * connection, so coming back is a question rather than a restart: how much is
 * left, is anything still going, and would the user like to finish it.
 *
 * The user is asked before spending anything. Resuming calls the model, and a
 * run with nine pages left is nine model calls the user did not necessarily
 * mean to make.
 */

export interface ResumeState {
  status: CloneRunStatus | null;
  /** A status lookup or a resume request is in flight. */
  isWorking: boolean;
  /** The connection to the running job is open, or was refused. */
  isFollowing: boolean;
  error: string | null;
}

export interface UseCloneResume {
  state: ResumeState;
  /** True when a resume would generate something rather than just watch. */
  canResume: boolean;
  resume: () => Promise<void>;
  stop: () => Promise<void>;
  refresh: () => Promise<void>;
}

const IDLE: ResumeState = {
  status: null,
  isWorking: false,
  isFollowing: false,
  error: null,
};

export function useCloneResume(
  runId: string | null,
  settings: Settings,
  onCode: (code: Record<string, string>) => void
): UseCloneResume {
  const [state, setState] = useState<ResumeState>(IDLE);
  // A status answer for a run the user has moved on from must not be shown
  // against the one they are looking at now.
  const runRef = useRef<string | null>(runId);
  const onCodeRef = useRef(onCode);
  onCodeRef.current = onCode;

  useEffect(() => {
    if (runRef.current !== runId) {
      runRef.current = runId;
      setState(IDLE);
    }
  }, [runId]);

  const refresh = useCallback(async () => {
    if (!runId) return;
    try {
      const status = await fetchCloneRunStatus(runId);
      if (runRef.current !== runId) return;
      setState((previous) => ({ ...previous, status, error: null }));
    } catch (error) {
      if (runRef.current !== runId) return;
      // A run the backend has forgotten is not an error worth a toast every
      // time the preview opens: it just means there is nothing to resume.
      setState((previous) => ({ ...previous, error: describe(error) }));
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // While a run is going, watch it. Leaving this socket open is what makes a
  // second tab - or the same tab after a reload - see the pages appear.
  const following = state.status?.running === true;
  useEffect(() => {
    if (!runId || !following) {
      setState((previous) => ({ ...previous, isFollowing: false }));
      return;
    }
    let socket: WebSocket | null = null;
    let closed = false;

    try {
      socket = new WebSocket(`${WS_BACKEND_URL}/url-to-code/${encodeURIComponent(runId)}/follow`);
    } catch {
      return;
    }
    socket.onopen = () => {
      if (!closed) setState((previous) => ({ ...previous, isFollowing: true }));
    };
    socket.onmessage = (message) => {
      if (closed) return;
      try {
        const payload = JSON.parse(String(message.data)) as {
          type?: string;
          data?: { code?: Record<string, string> };
        };
        if (payload.data?.code && Object.keys(payload.data.code).length > 0) {
          onCodeRef.current(payload.data.code);
        }
        if (payload.type === "error") {
          setState((previous) => ({ ...previous, isFollowing: false }));
        }
      } catch {
        // A message that is not the shape expected is not worth reporting;
        // the next one will be.
      }
    };
    socket.onclose = () => {
      if (!closed) setState((previous) => ({ ...previous, isFollowing: false }));
    };
    socket.onerror = () => {
      if (!closed) setState((previous) => ({ ...previous, isFollowing: false }));
    };

    return () => {
      closed = true;
      // Closing the watch does not stop the run: that is the point of it
      // being a background job.
      try {
        socket?.close();
      } catch {
        // Already closing.
      }
    };
  }, [runId, following]);

  const resume = useCallback(async () => {
    if (!runId) {
      toast.error("This clone has no stored run to continue.");
      return;
    }
    setState((previous) => ({ ...previous, isWorking: true, error: null }));
    try {
      const status = await resumeCloneRun(runId, settings);
      if (runRef.current !== runId) return;
      setState((previous) => ({ ...previous, status, isWorking: false }));

      if (status.running) {
        const pending = status.pagesPending ?? 0;
        toast(
          pending
            ? `Continuing — ${pending} page${pending === 1 ? "" : "s"} left to generate.`
            : "Continuing the run.",
          { icon: "▶️", duration: 5000 }
        );
      } else {
        toast.success("This clone was already finished.");
      }
    } catch (error) {
      if (runRef.current !== runId) return;
      const message = describe(error);
      setState((previous) => ({ ...previous, isWorking: false, error: message }));
      toast.error(message, { duration: 7000 });
    }
  }, [runId, settings]);

  const stop = useCallback(async () => {
    if (!runId) return;
    try {
      const stopped = await stopCloneRun(runId);
      if (runRef.current !== runId) return;
      await refresh();
      toast(stopped ? "Stopped. Everything it had finished is kept." : "It was not running.", {
        duration: 5000,
      });
    } catch (error) {
      if (runRef.current !== runId) return;
      const message = describe(error);
      setState((previous) => ({ ...previous, error: message }));
      toast.error(message, { duration: 7000 });
    }
  }, [runId, refresh]);

  const canResume =
    state.status?.known === true &&
    (state.status.pagesPending ?? 0) > 0 &&
    !state.status.running;

  return { state, canResume, resume, stop, refresh };
}

function describe(error: unknown): string {
  if (error instanceof CloneRunError || error instanceof Error) return error.message;
  return "The run could not be continued.";
}
