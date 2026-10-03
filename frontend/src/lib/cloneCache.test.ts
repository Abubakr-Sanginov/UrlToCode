// config.ts reads `import.meta.env`, which is Vite syntax and does not exist
// under the CJS transform jest runs. The URL is the only thing taken from it,
// so stubbing it keeps the rest of the module under test for real.
jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://backend.test",
}));

import { CloneCacheError, clearCloneCache, describeCacheSize, fetchCloneCache } from "./cloneCache";

function mockFetch(body: unknown, status = 200) {
  const calls: [string, RequestInit][] = [];
  (global as { fetch?: unknown }).fetch = jest.fn(async (url: string, init: RequestInit) => {
    calls.push([url, init ?? {}]);
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    } as unknown as Response;
  });
  return calls;
}

afterEach(() => {
  delete (global as { fetch?: unknown }).fetch;
});

describe("the generation cache", () => {
  it("asks the backend what it is holding", async () => {
    const calls = mockFetch({ entries: 12, bytes: 2048, oldest: 1_700_000_000 });

    const info = await fetchCloneCache();

    expect(info.entries).toBe(12);
    expect(calls[0][0]).toBe("http://backend.test/api/clone-cache");
  });

  it("reads an answer that leaves numbers out rather than showing NaN", async () => {
    // A backend that has never cached anything says so with an empty object;
    // rendering "undefined" in the settings panel is not an answer.
    mockFetch({});

    expect(await fetchCloneCache()).toEqual({
      entries: 0,
      bytes: 0,
      oldest: 0,
      directory: "",
    });
  });

  it("empties the cache with a delete", async () => {
    const calls = mockFetch({ removed: 7 });

    const result = await clearCloneCache();

    expect(result.removed).toBe(7);
    const [url, init] = calls[0];
    expect(url).toBe("http://backend.test/api/clone-cache");
    expect(init.method).toBe("DELETE");
  });

  it("reports the server's own reason rather than a bare status", async () => {
    mockFetch({ detail: "The cache directory is not writable." }, 500);

    await expect(fetchCloneCache()).rejects.toThrow("not writable");
  });

  it("fails with its own type so callers can tell it apart", async () => {
    mockFetch({}, 503);

    await expect(clearCloneCache()).rejects.toBeInstanceOf(CloneCacheError);
  });
});

describe("describeCacheSize", () => {
  it("speaks in units a person would use", () => {
    expect(describeCacheSize(512)).toBe("512 B");
    expect(describeCacheSize(2048)).toBe("2 KB");
    expect(describeCacheSize(3 * 1024 * 1024)).toBe("3.0 MB");
  });

  it("does not show a negative number for a size it cannot read", () => {
    expect(describeCacheSize(0)).toBe("0 B");
  });
});
