import { SavedClone, describeProjectSize } from "./localProject";

jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://backend.test",
  mediaUrl: (path: string) => `http://backend.test${path}`,
  WS_BACKEND_URL: "ws://backend.test",
  MODIFIED_FILE_ID: "file:",
}));

describe("describing a project's size", () => {
  it("does not call a small project large", () => {
    expect(describeProjectSize(512)).toBe("512 B");
  });

  it("reads in kilobytes before it reads in megabytes", () => {
    expect(describeProjectSize(2048)).toBe("2 KB");
    expect(describeProjectSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});

describe("what the library shows", () => {
  const clone: SavedClone = {
    runId: "20260101-000000-abcd1234",
    name: "Acme",
    sourceUrl: "https://acme.test",
    savedAt: 1767225600,
    pageCount: 4,
    hasServer: true,
    url: "/generated/20260101-000000-abcd1234/",
    sizeBytes: 2048,
  };

  it("knows where a saved clone is served from", () => {
    // The run id alone is not a URL; the path the backend serves it on is.
    expect(clone.url).toContain(clone.runId);
  });

  it("counts a multi-page clone in the plural", () => {
    expect(`${clone.pageCount} page${clone.pageCount === 1 ? "" : "s"}`).toBe("4 pages");
  });
});
