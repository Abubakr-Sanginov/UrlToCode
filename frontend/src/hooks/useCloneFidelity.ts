import { useCallback, useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import {
  CloneRunError,
  PageFidelity,
  RunFidelity,
  checkCloneFidelity,
  repairCloneFidelity,
} from "../lib/cloneRuns";
import { Settings } from "../types";

/**
 * How close the clone is to the site it was made from, page by page.
 *
 * Checking costs a browser render per page on the backend, and repairing costs
 * a model call per page, so neither happens on its own: the user asks for a
 * check, sees the numbers, and then decides which pages are worth a repair.
 * The result is kept here rather than in the project store because it describes
 * the run, not the code the user is editing - though the repairs it produces do
 * land in the project's file overrides.
 */

export interface FidelityState {
  /** The last check, or null when this session has not run one. */
  result: RunFidelity | null;
  isChecking: boolean;
  isRepairing: boolean;
  /** True while an older check is in flight and its result is stale. */
  error: string | null;
}

export interface UseCloneFidelity {
  state: FidelityState;
  check: (paths?: string[]) => Promise<void>;
  repair: (paths?: string[]) => Promise<Record<string, string> | null>;
  reset: () => void;
}

const IDLE: FidelityState = {
  result: null,
  isChecking: false,
  isRepairing: false,
  error: null,
};

export function useCloneFidelity(
  runId: string | null,
  settings: Settings,
  onRepaired: (code: Record<string, string>) => void
): UseCloneFidelity {
  const [state, setState] = useState<FidelityState>(IDLE);
  // A run that is gone from the client is not the same run the user checked,
  // so a late answer from the previous run must not be shown for this one.
  const runRef = useRef<string | null>(runId);

  useEffect(() => {
    if (runRef.current !== runId) {
      runRef.current = runId;
      setState(IDLE);
    }
  }, [runId]);

  const check = useCallback(
    async (paths?: string[]) => {
      if (!runId) {
        toast.error("This clone has no stored run to check.");
        return;
      }
      setState((previous) => ({ ...previous, isChecking: true, error: null }));
      try {
        const result = await checkCloneFidelity({ runId, paths });
        if (runRef.current !== runId) return;
        setState({ result, isChecking: false, isRepairing: false, error: null });

        if (result.skipped.length && !result.pages.length) {
          toast(
            "Nothing could be compared here: no page of this clone can be rendered on the server.",
            { icon: "⚠️", duration: 6000 }
          );
        } else if (result.pages.length) {
          const pct = Math.round(result.meanScore * 100);
          toast.success(
            `${result.pages.length} page${result.pages.length === 1 ? "" : "s"} checked — ` +
              `${pct}% average match`,
            { duration: 4000 }
          );
        }
      } catch (error) {
        if (runRef.current !== runId) return;
        const message =
          error instanceof Error ? error.message : "The clone could not be checked.";
        setState({ result: null, isChecking: false, isRepairing: false, error: message });
        toast.error(message, { duration: 6000 });
      }
    },
    [runId]
  );

  const repair = useCallback(
    async (paths?: string[]): Promise<Record<string, string> | null> => {
      if (!runId) {
        toast.error("This clone has no stored run to repair.");
        return null;
      }
      setState((previous) => ({ ...previous, isRepairing: true, error: null }));
      try {
        const outcome = await repairCloneFidelity({ runId, settings, paths });
        if (runRef.current !== runId) return null;

        onRepaired(outcome.code);
        // The repairs changed the pages, so the scores on hand are stale until
        // the pages are looked at again.
        setState({
          result: null,
          isChecking: false,
          isRepairing: false,
          error: null,
        });

        const passed = outcome.repaired.filter((page) => page.passed).length;
        if (passed) {
          toast.success(
            `${passed} of ${outcome.repaired.length} repaired page${
              outcome.repaired.length === 1 ? "" : "s"
            } now match the original`,
            { duration: 5000 }
          );
        } else if (!outcome.repaired.length) {
          toast("No page was below the threshold, so nothing was regenerated.", {
            duration: 4000,
          });
        } else {
          toast.error(
            "The pages were regenerated but still do not match. Check them in Compare.",
            { duration: 7000 }
          );
        }
        if (outcome.stopped) toast(outcome.stopped, { duration: 7000 });
        return outcome.code;
      } catch (error) {
        if (runRef.current !== runId) return null;
        const message =
          error instanceof CloneRunError || error instanceof Error
            ? error.message
            : "The pages could not be repaired.";
        setState((previous) => ({ ...previous, isRepairing: false, error: message }));
        toast.error(message, { duration: 7000 });
        return null;
      }
    },
    [runId, settings, onRepaired]
  );

  const reset = useCallback(() => setState(IDLE), []);

  return { state, check, repair, reset };
}

/** The fidelity record for one page in a check, if there is one. */
export function fidelityFor(
  result: RunFidelity | null,
  path: string | null
): PageFidelity | null {
  if (!result || !path) return null;
  return result.pages.find((page) => page.path === path) ?? null;
}
