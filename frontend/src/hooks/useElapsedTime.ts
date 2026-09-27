import { useEffect, useState } from "react";

/**
 * Formats a duration the same way the rest of the app does: seconds under a
 * minute, then `m:ss`.
 */
export function formatElapsed(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

/**
 * Ticks once per second while `startedAt` is set and `isRunning` is true, and
 * freezes at the final value once the run stops. Returns null before a run.
 */
export function useElapsedTime(
  startedAt: number | null,
  isRunning: boolean
): string | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!isRunning || startedAt === null) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [isRunning, startedAt]);

  if (startedAt === null) return null;
  // Once stopped, `now` holds the last tick, so the label freezes.
  return formatElapsed(Math.max(0, now - startedAt));
}
