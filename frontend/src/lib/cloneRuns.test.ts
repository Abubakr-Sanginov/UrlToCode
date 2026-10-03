// config.ts reads `import.meta.env`, which is Vite syntax and does not exist
// under the CJS transform jest runs. The URL is the only thing taken from it,
// so stubbing it keeps the rest of the module under test for real.
jest.mock("../config", () => ({
  HTTP_BACKEND_URL: "http://backend.test",
  MEDIA_ROUTE: "/crawl-assets",
  mediaUrl: (name: string) => `http://backend.test/crawl-assets/${name}`,
}));

import {
  CloneRunError,
  checkCloneFidelity,
  detectCloneVision,
  estimateCloneCost,
  fetchCloneRunStatus,
  fidelityLabel,
  fidelityVerdict,
  repairCloneFidelity,
  resumeCloneRun,
  stopCloneRun,
} from "./cloneRuns";
import { Settings } from "../types";

const settings = {
  openRouterApiKey: "sk-test",
  openRouterModel: "test/model",
  reasoningEffort: "low",
  openAiApiKey: null,
  openAiBaseURL: null,
  anthropicApiKey: null,
  geminiApiKey: null,
  customProviderBaseUrl: null,
  customProviderApiKey: null,
  customProviderModel: null,
} as unknown as Settings;

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

/** A fetch stand-in that records the calls it was given. */
type FetchCall = [string, RequestInit];

function mockFetch(body: unknown, status = 200) {
  const calls: FetchCall[] = [];
  const fetchMock = jest.fn(async (url: string, init: RequestInit) => {
    calls.push([url, init]);
    return jsonResponse(body, status);
  });
  (global as { fetch?: unknown }).fetch = fetchMock;
  return calls;
}

describe("checkCloneFidelity", () => {
  afterEach(() => {
    delete (global as { fetch?: unknown }).fetch;
  });

  it("asks the backend to check the given pages", async () => {
    const calls = mockFetch({
      runId: "r1",
      pages: [],
      skipped: [],
      meanScore: 0,
      threshold: 0.8,
    });

    const result = await checkCloneFidelity({ runId: "r 1", paths: ["/about"] });

    expect(result.runId).toBe("r1");
    const [url, init] = calls[0];
    expect(url).toBe("http://backend.test/api/clone-runs/r%201/visual-check");
    expect(JSON.parse(String(init.body))).toEqual({ paths: ["/about"], threshold: null });
  });

  it("checks every page when none are named", async () => {
    const calls = mockFetch({ pages: [] });

    await checkCloneFidelity({ runId: "r1" });

    expect(JSON.parse(String(calls[0][1].body)).paths).toBeNull();
  });

  it("surfaces the server's own reason when a check cannot run", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () =>
      jsonResponse({ detail: "No such clone run." }, 404)
    );

    await expect(checkCloneFidelity({ runId: "gone" })).rejects.toThrow(
      "No such clone run."
    );
  });

  it("falls back to a usable shape when the response has no pages", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () => jsonResponse({}));

    const result = await checkCloneFidelity({ runId: "r1" });

    expect(result.pages).toEqual([]);
    expect(result.skipped).toEqual([]);
    expect(result.threshold).toBe(0.8);
  });

  it("rebuilds a desktop viewport for a run checked before multi-width", async () => {
    // An older run's page has no `viewports` list, and the diff view iterates
    // it - an empty array would silently hide the one result that exists.
    (global as { fetch?: unknown }).fetch = jest.fn(async () =>
      jsonResponse({
        pages: [{ path: "/", score: 0.9, renderFile: "a.png", originalHeight: 1200 }],
      })
    );

    const result = await checkCloneFidelity({ runId: "r1" });

    expect(result.pages[0].viewports).toHaveLength(1);
    expect(result.pages[0].viewports[0].name).toBe("desktop");
    expect(result.pages[0].viewports[0].score).toBe(0.9);
  });

  it("leaves the viewport list empty for a page that was never checked", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () =>
      jsonResponse({ pages: [{ path: "/contact", score: 0, error: "no renderer" }] })
    );

    const result = await checkCloneFidelity({ runId: "r1" });

    expect(result.pages[0].viewports).toEqual([]);
  });

  it("keeps every width a responsive run was checked at", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () =>
      jsonResponse({
        pages: [
          {
            path: "/",
            score: 0.2,
            viewports: [
              { name: "desktop", width: 1440, score: 0.97 },
              { name: "mobile", width: 375, score: 0.2 },
            ],
          },
        ],
      })
    );

    const result = await checkCloneFidelity({ runId: "r1" });

    expect(result.pages[0].viewports.map((v) => v.name)).toEqual(["desktop", "mobile"]);
    expect(result.pages[0].viewports[1].width).toBe(375);
  });
});

describe("repairCloneFidelity", () => {
  afterEach(() => {
    delete (global as { fetch?: unknown }).fetch;
  });

  it("sends the provider credentials and a page budget", async () => {
    const calls = mockFetch({ repaired: [], skipped: [], stopped: "", code: {} });

    await repairCloneFidelity({ runId: "r1", settings, maxPages: 2 });

    const [url, init] = calls[0];
    expect(url).toBe("http://backend.test/api/clone-runs/r1/repair");
    const body = JSON.parse(String(init.body));
    expect(body.maxPages).toBe(2);
    expect(body.maxAttempts).toBe(2);
    expect(body.openRouterApiKey).toBe("sk-test");
  });

  it("returns the run's code so the preview can be updated", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () =>
      jsonResponse({
        repaired: [{ path: "/about", attempt: 1, score: 0.9, passed: true, error: "" }],
        skipped: [],
        stopped: "",
        code: { "/about": "<html>fixed</html>" },
      })
    );

    const outcome = await repairCloneFidelity({ runId: "r1", settings });

    expect(outcome.code["/about"]).toBe("<html>fixed</html>");
    expect(outcome.repaired[0].passed).toBe(true);
  });

  it("explains a refusal rather than reporting a blank failure", async () => {
    (global as { fetch?: unknown }).fetch = jest.fn(async () =>
      jsonResponse({ detail: "No model provider configured. Add a key in Settings." }, 400)
    );

    await expect(repairCloneFidelity({ runId: "r1", settings })).rejects.toThrow(
      CloneRunError
    );
  });
});

describe("fidelityVerdict", () => {
  it("calls a near-identical page close", () => {
    expect(fidelityVerdict(0.98)).toBe("close");
  });

  it("calls a page above the bar close enough", () => {
    expect(fidelityVerdict(0.85)).toBe("close-enough");
  });

  it("treats near-identical as an absolute bar, not one the threshold moves", () => {
    // The threshold decides what is worth repairing; it says nothing about
    // what "identical" means, so lowering it must not promote a page to it.
    expect(fidelityVerdict(0.8, 0.7)).toBe("close-enough");
    expect(fidelityVerdict(0.96, 0.99)).toBe("close");
  });

  it("calls a page below the bar off", () => {
    expect(fidelityVerdict(0.5)).toBe("off");
  });

  it("does not call an unchecked page wrong", () => {
    expect(fidelityVerdict(null)).toBe("unknown");
    expect(fidelityVerdict(Number.NaN)).toBe("unknown");
  });

  it("has a label for every verdict", () => {
    for (const verdict of ["close", "close-enough", "off", "unknown"] as const) {
      expect(fidelityLabel(verdict)).toBeTruthy();
    }
  });
});

describe("carrying a run on", () => {
  afterEach(() => {
    delete (global as { fetch?: unknown }).fetch;
  });

  it("asks how much of a run is left", async () => {
    const calls = mockFetch({ runId: "r1", known: true, running: false, pagesPending: 3 });

    const status = await fetchCloneRunStatus("r 1");

    expect(status.pagesPending).toBe(3);
    expect(calls[0][0]).toBe("http://backend.test/api/clone-runs/r%201/status");
  });

  it("says a run the backend has forgotten is not known", async () => {
    mockFetch({ runId: "gone", known: false, running: false });

    const status = await fetchCloneRunStatus("gone");

    expect(status.known).toBe(false);
  });

  it("sends the key with a resume, and the key goes nowhere else", async () => {
    // The key is sent once, on the request that needs it. The run on the
    // server keeps the provider, not the credential.
    const calls = mockFetch({ runId: "r1", known: true, running: true, pagesPending: 2 });

    const status = await resumeCloneRun("r1", settings);

    expect(status.running).toBe(true);
    const [url, init] = calls[0];
    expect(url).toBe("http://backend.test/api/clone-runs/r1/resume");
    expect(init.method).toBe("POST");
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body.openRouterApiKey).toBe("sk-test");
  });

  it("reports the server's own reason when a resume is refused", async () => {
    mockFetch({ detail: "This run has no stored crawl to generate from." }, 409);

    await expect(resumeCloneRun("r1", settings)).rejects.toThrow("no stored crawl");
  });

  it("says whether stopping actually stopped anything", async () => {
    mockFetch({ runId: "r1", stopped: false });

    expect(await stopCloneRun("r1")).toBe(false);
  });
});

describe("asking what a clone will cost", () => {
  afterEach(() => {
    delete (global as { fetch?: unknown }).fetch;
  });

  it("asks about the size that was actually requested", async () => {
    const calls = mockFetch({
      pages: {
        pages: 4,
        model: "gpt-4o",
        perPage: 0.09,
        perPageText: "$0.09",
        total: 0.36,
        totalText: "$0.36",
      },
      routing: { pageModel: "gpt-4o", auxiliaryModel: "gpt-4o", reasons: {} },
    });

    const estimate = await estimateCloneCost({ ...settings, url: "https://x.test", maxPages: 4 });

    expect(estimate.pages.totalText).toBe("$0.36");
    const [url, init] = calls[0];
    expect(url).toBe("http://backend.test/api/clone-cost/estimate");
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    expect(body.maxPages).toBe(4);
    expect(body.url).toBe("https://x.test");
  });

  it("sends the keys because the estimate is priced per model", async () => {
    const calls = mockFetch({ pages: {}, routing: {} });

    await estimateCloneCost({ ...settings, url: "https://x.test", maxPages: 1 });

    const body = JSON.parse(String(calls[0][1].body)) as Record<string, unknown>;
    expect(body.openRouterApiKey).toBe("sk-test");
  });

  it("reports the server's own reason when it cannot price a model", async () => {
    mockFetch({ detail: "No model provider configured." }, 400);

    await expect(
      estimateCloneCost({ ...settings, url: "https://x.test", maxPages: 1 })
    ).rejects.toThrow("No model provider configured");
  });

  it("passes on whether the model can see images", async () => {
    mockFetch({
      pages: {},
      routing: { vision: "yes", seesImages: true },
      vision: { model: "gpt-4o", capability: "yes", source: "probe", detail: "" },
    });

    const estimate = await estimateCloneCost({
      ...settings,
      url: "https://x.test",
      maxPages: 1,
    });

    expect(estimate.vision.capability).toBe("yes");
    expect(estimate.routing.seesImages).toBe(true);
  });

  it("keeps an unconfirmed answer apart from a negative one", async () => {
    // These send the user to different places: one to change model, the
    // other to fix the key.
    mockFetch({
      pages: {},
      routing: { vision: "unknown", seesImages: false },
      vision: {
        model: "x",
        capability: "unknown",
        source: "probe",
        detail: "subscription required",
      },
    });

    const estimate = await estimateCloneCost({
      ...settings,
      url: "https://x.test",
      maxPages: 1,
    });

    expect(estimate.vision.capability).not.toBe("no");
    expect(estimate.vision.detail).toBe("subscription required");
  });
  it("carries a user's own endpoint into the estimate", async () => {
    // Without these the estimate - and the vision badge the UI reads from
    // it - would be about a model the user did not choose.
    const calls = mockFetch({
      pages: {},
      routing: { vision: "yes", seesImages: true },
      vision: { model: "llava", capability: "yes", source: "probe", detail: "" },
    });

    await estimateCloneCost({
      ...settings,
      url: "https://x.test",
      maxPages: 1,
      customProviderBaseUrl: "http://127.0.0.1:1234/v1",
      customProviderApiKey: "sk-own",
      customProviderModel: "llava-v1.6",
    });

    const body = JSON.parse(String(calls[0][1].body)) as Record<string, unknown>;
    expect(body.customProviderBaseUrl).toBe("http://127.0.0.1:1234/v1");
    expect(body.customProviderModel).toBe("llava-v1.6");
    expect(body.customProviderApiKey).toBe("sk-own");
  });

  it("carries the reasoning effort so the setting is not a local guess", async () => {
    // Left behind in Settings and not sent, a reasoning model keeps
    // thinking until it has no budget left to answer with.
    const calls = mockFetch({
      pages: {},
      routing: { vision: "unknown", seesImages: false },
      vision: { model: "x", capability: "unknown", source: "probe", detail: "" },
    });

    await estimateCloneCost({
      ...settings,
      url: "https://x.test",
      maxPages: 1,
    });

    const body = JSON.parse(String(calls[0][1].body)) as Record<string, unknown>;
    expect(body.reasoningEffort).toBe("low");
  });
});

describe("asking whether a model can see", () => {
  afterEach(() => {
    delete (global as { fetch?: unknown }).fetch;
  });

  it("asks the model rather than guessing from its name", async () => {
    const calls = mockFetch({
      model: "made-up",
      capability: "yes",
      source: "probe",
      detail: "",
    });

    await detectCloneVision(settings);

    const body = JSON.parse(String(calls[0][1].body)) as Record<string, unknown>;
    expect(body.refresh).toBe(false);
    expect(body.openRouterApiKey).toBe("sk-test");
  });

  it("can be asked to check again on purpose", async () => {
    const calls = mockFetch({
      model: "made-up",
      capability: "no",
      source: "probe",
      detail: "",
    });

    await detectCloneVision(settings, true);

    const body = JSON.parse(String(calls[0][1].body)) as Record<string, unknown>;
    expect(body.refresh).toBe(true);
  });
});
