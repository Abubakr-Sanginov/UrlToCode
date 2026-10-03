import { Commit, VariantStatus } from "../components/commits/types";
import { useAppStore } from "./app-store";
import { reconcileRestoredProject, useProjectStore } from "./project-store";

function createGeneratingCommit(): Commit {
  return {
    hash: "timed-commit",
    parentHash: null,
    dateCreated: new Date(1_000),
    isCommitted: false,
    variants: [{ code: "", history: [] }],
    selectedVariantIndex: 0,
    type: "ai_create",
    inputs: { text: "Create a page", images: [] },
  };
}

describe("version navigation", () => {
  const selectedElement = { tagName: "BUTTON" } as HTMLElement;

  beforeEach(() => {
    useProjectStore.setState({ head: "latest" });
    useAppStore.setState({
      inSelectAndEditMode: true,
      selectedElement,
    });
  });

  afterEach(() => {
    useAppStore.setState({
      inSelectAndEditMode: false,
      selectedElement: null,
    });
  });

  it("exits select-and-edit and clears its target when the head changes", () => {
    useProjectStore.getState().setHead("previous");

    expect(useProjectStore.getState().head).toBe("previous");
    expect(useAppStore.getState().inSelectAndEditMode).toBe(false);
    expect(useAppStore.getState().selectedElement).toBeNull();
  });

  it("does not exit select-and-edit when the requested head is already active", () => {
    useProjectStore.getState().setHead("latest");

    expect(useAppStore.getState().inSelectAndEditMode).toBe(true);
    expect(useAppStore.getState().selectedElement).toBe(selectedElement);
  });
});

describe("variant completion timestamps", () => {
  beforeEach(() => {
    useProjectStore.setState({
      commits: {},
      head: null,
      latestCommitHash: null,
    });
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  test.each<VariantStatus>(["complete", "error", "cancelled"])(
    "records one stable timestamp when a variant becomes %s",
    (status) => {
      const now = jest.spyOn(Date, "now").mockReturnValue(116_000);
      const store = useProjectStore.getState();
      store.addCommit(createGeneratingCommit());
      store.updateVariantStatus("timed-commit", 0, status);

      expect(
        useProjectStore.getState().commits["timed-commit"].variants[0]
          .completedAt
      ).toBe(116_000);

      now.mockReturnValue(999_000);
      store.updateVariantStatus("timed-commit", 0, status);

      expect(
        useProjectStore.getState().commits["timed-commit"].variants[0]
          .completedAt
      ).toBe(116_000);
    }
  );
});

describe("project restore", () => {
  const current = () => useProjectStore.getState();

  it("keeps stored commits and revives dates into real Date objects", () => {
    const restored = reconcileRestoredProject(
      {
        commits: {
          "/": {
            hash: "/",
            parentHash: null,
            dateCreated: "2026-09-27T08:05:12.000Z",
            isCommitted: true,
            type: "code_create",
            inputs: null,
            label: "/",
            selectedVariantIndex: 0,
            variants: [{ code: "<html></html>", history: [], status: "complete" }],
          },
        },
        head: "/",
        latestCommitHash: "/",
      },
      current()
    );

    expect(Object.keys(restored.commits)).toEqual(["/"]);
    expect(restored.commits["/"].dateCreated).toBeInstanceOf(Date);
    expect(restored.commits["/"].dateCreated.toISOString()).toBe(
      "2026-09-27T08:05:12.000Z"
    );
    expect(restored.head).toBe("/");
  });

  it("marks a generation that was still streaming as an error, not a spinner", () => {
    const restored = reconcileRestoredProject(
      {
        commits: {
          c1: {
            hash: "c1",
            dateCreated: 1_000,
            variants: [
              {
                code: "<html>",
                status: "generating",
                thinkingStartTime: 1_000,
              },
            ],
          },
        },
        head: "c1",
      },
      current()
    );

    const variant = restored.commits["c1"].variants[0];
    expect(variant.status).toBe("error");
    expect(variant.errorMessage).toMatch(/reloaded/i);
    expect(variant.thinkingStartTime).toBeUndefined();
    // The partial code that did arrive is still there to look at.
    expect(variant.code).toBe("<html>");
  });

  it("drops a head that no longer resolves to a commit", () => {
    const restored = reconcileRestoredProject(
      {
        commits: {
          c1: { hash: "c1", dateCreated: 1_000, variants: [{ code: "a" }] },
        },
        head: "gone",
        latestCommitHash: "gone",
      },
      current()
    );

    expect(restored.head).toBeNull();
    expect(restored.latestCommitHash).toBeNull();
    expect(Object.keys(restored.commits)).toEqual(["c1"]);
  });

  it("survives junk on disk without throwing", () => {
    // Start from a clean project so leftovers from earlier cases cannot
    // masquerade as restored state.
    useProjectStore.setState({ commits: {}, head: null, latestCommitHash: null });
    const clean = useProjectStore.getState();

    for (const junk of [
      null,
      "nope",
      42,
      { commits: "bad" },
      { commits: { a: {} } },
      { commits: { a: { hash: "a", variants: [] } } },
    ]) {
      const restored = reconcileRestoredProject(junk, clean);
      expect(restored.commits).toEqual({});
      expect(restored.head).toBeNull();
    }
  });

  it("clamps a selected variant index that points past the variant list", () => {
    const restored = reconcileRestoredProject(
      {
        commits: {
          c1: {
            hash: "c1",
            dateCreated: 1_000,
            selectedVariantIndex: 7,
            variants: [{ code: "a" }, { code: "b" }],
          },
        },
      },
      current()
    );

    expect(restored.commits["c1"].selectedVariantIndex).toBe(1);
  });
});
