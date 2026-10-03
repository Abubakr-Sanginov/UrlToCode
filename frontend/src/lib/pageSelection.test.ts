import {
  needsChoosing,
  pickAll,
  pickNone,
  resolvedPaths,
  selectionSummary,
  togglePicked,
} from "./pageSelection";
import type { SelectablePage } from "../hooks/useUrlToCode";

const pages: SelectablePage[] = [
  { path: "/", title: "Home", url: "https://shop.test/", depth: 0 },
  { path: "/about", title: "About", url: "https://shop.test/about", depth: 1 },
  { path: "/blog/post-1", title: "Post", url: "https://shop.test/blog/post-1", depth: 2 },
];

describe("ticking pages", () => {
  it("ticks a page on", () => {
    expect([...togglePicked(new Set(), "/about")]).toEqual(["/about"]);
  });

  it("ticks a ticked page off", () => {
    expect([...togglePicked(new Set(["/about"]), "/about")]).toEqual([]);
  });

  it("does not change the set it was given", () => {
    // React state is compared by identity: mutating it in place would render
    // nothing and leave the checkbox looking untouched.
    const before = new Set(["/"]);
    togglePicked(before, "/about");
    expect([...before]).toEqual(["/"]);
  });

  it("ticks every page at once", () => {
    expect([...pickAll(pages)].sort()).toEqual(["/", "/about", "/blog/post-1"]);
  });

  it("unticks everything at once", () => {
    expect([...pickNone()]).toEqual([]);
  });
});

describe("what the button says", () => {
  it("counts what is ticked", () => {
    expect(selectionSummary(new Set(["/"]))).toBe("Build 1 page");
    expect(selectionSummary(new Set(["/", "/about"]))).toBe("Build 2 pages");
  });

  it("says what happens when nothing is ticked, rather than nothing", () => {
    // An empty selection is a decision. A button that read "Build" would
    // leave the user unsure whether the run had started.
    expect(selectionSummary(new Set())).toBe("Build none");
  });
});

describe("which pages get generated", () => {
  it("keeps the order the crawl found them in", () => {
    expect(resolvedPaths(new Set(["/blog/post-1", "/"]), pages)).toEqual([
      "/",
      "/blog/post-1",
    ]);
  });

  it("ignores a ticked page the crawl never found", () => {
    // Generating a page nobody crawled would fail after the user had paid
    // for the attempt.
    expect(resolvedPaths(new Set(["/", "/admin"]), pages)).toEqual(["/"]);
  });

  it("generates nothing when nothing is ticked", () => {
    expect(resolvedPaths(new Set(), pages)).toEqual([]);
  });
});

describe("whether to ask at all", () => {
  it("asks when the user asked and there is a choice to make", () => {
    expect(needsChoosing(true, 4)).toBe(true);
  });

  it("does not ask for a site with a single page", () => {
    // One possible answer is an obstacle, not a choice.
    expect(needsChoosing(true, 1)).toBe(false);
  });

  it("does not ask when the user did not ask for it", () => {
    expect(needsChoosing(false, 40)).toBe(false);
  });
});
