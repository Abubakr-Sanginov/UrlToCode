import { buildProjectFiles, generatedPathFor, projectFileForPage } from "./projectFiles";

const HOME = "<!DOCTYPE html><html><head><title>Acme</title></head><body>home</body></html>";
const ABOUT = "<!DOCTYPE html><html><head><title>Acme</title></head><body>about</body></html>";

describe("buildProjectFiles", () => {
  it("returns nothing for an empty code map", () => {
    expect(buildProjectFiles({})).toEqual([]);
    expect(buildProjectFiles({ "/": "   " })).toEqual([]);
  });

  it("gives the crawled root page index.html and slugs the rest", () => {
    const files = buildProjectFiles({
      "project-structure": HOME,
      "/": HOME,
      "/About Us": ABOUT,
      "database-schema": "CREATE TABLE t (id int);",
    });
    const paths = files.map((f) => f.path);

    expect(paths).toContain("index.html");
    expect(paths).toContain("about-us.html");
    expect(paths).toContain("portal.html");
    expect(paths).toContain("database-schema.sql");
    expect(paths).toContain("README.md");
  });

  it("uses the same file names whether or not overrides are given", () => {
    const code = { "/": HOME, "/about": ABOUT };
    expect(buildProjectFiles(code, "Acme").map((f) => f.path)).toEqual(
      buildProjectFiles(code, "Acme", {}).map((f) => f.path)
    );
  });

  it("writes an edited file into the project", () => {
    const files = buildProjectFiles(
      { "/": HOME, "/about": ABOUT },
      "Acme",
      { "about.html": "<html>EDITED</html>" }
    );
    const byPath = Object.fromEntries(files.map((f) => [f.path, f.content]));

    expect(byPath["about.html"]).toBe("<html>EDITED</html>");
    expect(byPath["index.html"]).toBe(HOME);
  });

  it("keeps an edited file the layout would not have produced", () => {
    const files = buildProjectFiles(
      { "/": HOME },
      "Acme",
      { "extra.html": "<html>new</html>" }
    );
    expect(files.map((f) => f.path)).toContain("extra.html");
  });
});

describe("the generated server and mock layer", () => {
  const EXTRAS = {
    "/": HOME,
    "file:server/app.py": "app = 1",
    "file:server/requirements.txt": "fastapi",
    "file:mock/api.json": "{}",
    "file:mock/mock.js": "// interceptor",
  };

  it("keeps each file in its own place", () => {
    // Flattening `server/app.py` would lose the layout the README's
    // `cd server` depends on.
    const paths = buildProjectFiles(EXTRAS, "Acme").map((f) => f.path);

    expect(paths).toContain("server/app.py");
    expect(paths).toContain("server/requirements.txt");
    expect(paths).toContain("mock/api.json");
    expect(paths).toContain("mock/mock.js");
  });

  it("tells the readme how to start the server", () => {
    const readme = buildProjectFiles(EXTRAS, "Acme").find((f) => f.path === "README.md");

    expect(readme?.content).toContain("cd server");
    expect(readme?.content).toContain("uvicorn");
  });

  it("tells the readme how to use the captured answers", () => {
    const readme = buildProjectFiles(EXTRAS, "Acme").find((f) => f.path === "README.md");

    expect(readme?.content).toContain("/mock/mock.js");
  });

  it("leaves the readme alone when there is neither", () => {
    const readme = buildProjectFiles({ "/": HOME }, "Acme").find((f) => f.path === "README.md");

    expect(readme?.content).not.toContain("uvicorn");
    expect(readme?.content).not.toContain("mock.js");
  });

  it("never writes a generated file outside the project folder", () => {
    // The path arrives over the wire. A key that climbs out of the folder
    // would put a file somewhere the user did not agree to create.
    const hostile = {
      "/": HOME,
      "file:../escape.py": "gotcha",
      "file:/etc/passwd": "gotcha",
      "file:C:\\Windows\\system32\\x": "gotcha",
    };

    const paths = buildProjectFiles(hostile, "Acme").map((f) => f.path);

    expect(paths).not.toContain("../escape.py");
    expect(paths.some((p) => p.includes("..") || p.startsWith("/") || p.includes("\\"))).toBe(
      false
    );
  });

  it("accepts only a path inside the project", () => {
    expect(generatedPathFor("file:server/app.py")).toBe("server/app.py");
    expect(generatedPathFor("file:")).toBeNull();
    expect(generatedPathFor("file:../x")).toBeNull();
    expect(generatedPathFor("file:/x")).toBeNull();
  });
});

describe("projectFileForPage", () => {
  it("finds the file a crawled page is saved to", () => {
    const code = { "project-structure": HOME, "/": HOME, "/About Us": ABOUT };

    expect(projectFileForPage(code, "/")).toBe("index.html");
    expect(projectFileForPage(code, "/About Us")).toBe("about-us.html");
    expect(projectFileForPage(code, "project-structure")).toBe("portal.html");
  });

  it("lets the portal own index.html when the crawl had no root page", () => {
    const code = { "project-structure": HOME, "/about": ABOUT };

    expect(projectFileForPage(code, "project-structure")).toBe("index.html");
    expect(projectFileForPage(code, "/about")).toBe("about.html");
  });

  it("is null for a page the project does not hold", () => {
    const code = { "/": HOME, "/about": ABOUT };

    expect(projectFileForPage(code, "/nope")).toBeNull();
    expect(projectFileForPage({}, "/")).toBeNull();
  });

  it("names the same file before and after an override is applied", () => {
    // A regeneration writes an override under this name, so the two calls
    // must not disagree about where the page lives.
    const code = { "/": HOME, "/about": ABOUT };
    const before = projectFileForPage(code, "/about");
    const files = buildProjectFiles(code, "Acme", {
      [before as string]: "<html>REGENERATED</html>",
    });
    const after = files.find((f) => f.content === "<html>REGENERATED</html>");

    expect(before).toBe("about.html");
    expect(after?.path).toBe(before);
  });
});
