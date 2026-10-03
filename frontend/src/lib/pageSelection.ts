import { PageSelection, SelectablePage } from "../hooks/useUrlToCode";

/**
 * Which of the crawled pages to build.
 *
 * One model call per page, so the whole point of this step is that a page
 * the user did not tick is a page nobody is charged for. That makes the
 * empty selection a real answer rather than a missing one: someone who
 * looked at forty pages and wanted none of them has decided, and the
 * backend has to be told so instead of guessing.
 */

/** Ticking one page on or off, without mutating the set that was passed in. */
export function togglePicked(picked: Set<string>, path: string): Set<string> {
  const next = new Set(picked);
  if (next.has(path)) next.delete(path);
  else next.add(path);
  return next;
}

/** Tick every page the crawl found. */
export function pickAll(pages: SelectablePage[]): Set<string> {
  return new Set(pages.map((page) => page.path));
}

export function pickNone(): Set<string> {
  return new Set();
}

/** What the button says, and what the user is about to be charged for. */
export function selectionSummary(picked: Set<string>): string {
  return picked.size
    ? `Build ${picked.size} page${picked.size === 1 ? "" : "s"}`
    : "Build none";
}

/**
 * The pages that will actually be generated, in the order the crawl found
 * them.
 *
 * Ticking a page that is not in the list changes nothing: a page the crawl
 * never saw has no HTML to rebuild from, and generating it would fail after
 * the user had already paid for the attempt.
 */
export function resolvedPaths(picked: Set<string>, pages: SelectablePage[]): string[] {
  return pages.filter((page) => picked.has(page.path)).map((page) => page.path);
}

/** Whether the question needs asking at all. */
export function needsChoosing(confirmPages: boolean, pageCount: number): boolean {
  return confirmPages && pageCount > 1;
}

export type { PageSelection, SelectablePage };
