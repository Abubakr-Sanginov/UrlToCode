import {
  applyFileOverrides,
  buildFrameworkProjectFiles,
  detectFramework,
  frameworkPageFileMap,
  parseFrameworkPage,
  rebuildPreviewSource,
  routeForPath,
} from "./frameworkProject";

// Same shape as backend/prompts/framework_stacks.py `wrap_component_preview`.
function preview(framework: string, route: string, source: string): string {
  const embedded = source.replace(/<\/(script)/gi, "<\\/$1");
  return `<!DOCTYPE html><html><head><title>Site</title></head><body><div id="root"></div>
  <script type="text/plain" id="urltocode-source" data-framework="${framework}" data-route="${route}">${embedded}</script>
  <script>/* runtime */</script></body></html>`;
}

const HOME = `"use client";\nexport default function Page() {\n  return <main>{"</script>"}</main>;\n}`;
const ABOUT = `"use client";\nexport default function Page() {\n  return <main>about</main>;\n}`;

describe("frameworkProject", () => {
  it("routes paths like the backend", () => {
    expect(routeForPath("/")).toBe("/");
    expect(routeForPath("/About Us/")).toBe("/about-us");
    expect(routeForPath("/blog/Post_1?x=1")).toBe("/blog/post-1");
  });

  it("extracts the embedded component source", () => {
    const page = parseFrameworkPage(preview("nextjs", "/", HOME));
    expect(page?.framework).toBe("nextjs");
    expect(page?.source).toBe(HOME);
    expect(parseFrameworkPage("<!DOCTYPE html><html></html>")).toBeNull();
  });

  it("lays out a Next.js project with a static preview", () => {
    const code = {
      "/": preview("nextjs", "/", HOME),
      "/about": preview("nextjs", "/about", ABOUT),
      "database-schema": "CREATE TABLE t (id int);",
    };
    expect(detectFramework(code)).toBe("nextjs");

    const files = buildFrameworkProjectFiles(code, "My Site")!;
    const byPath = Object.fromEntries(files.map((f) => [f.path, f.content]));

    expect(byPath["app/page.tsx"]).toBe(`${HOME}\n`);
    expect(byPath["app/about/page.tsx"]).toBe(`${ABOUT}\n`);
    expect(byPath["app/layout.tsx"]).toContain('title: "My Site"');
    expect(JSON.parse(byPath["package.json"]).dependencies.next).toBeDefined();
    expect(byPath["preview/index.html"]).toContain("urltocode-source");
    expect(byPath["preview/about.html"]).toBeDefined();
    expect(byPath["database-schema.sql"]).toContain("CREATE TABLE");
    expect(byPath["README.md"]).toContain("npm run dev");
  });

  it("lays out a React + Vite project routed by pathname", () => {
    const code = {
      "/": preview("react", "/", HOME),
      "/about": preview("react", "/about", ABOUT),
    };
    const files = buildFrameworkProjectFiles(code, "Site")!;
    const byPath = Object.fromEntries(files.map((f) => [f.path, f.content]));

    expect(byPath["src/pages/HomePage.tsx"]).toBe(`${HOME}\n`);
    expect(byPath["src/pages/AboutPage.tsx"]).toBe(`${ABOUT}\n`);
    expect(byPath["src/main.tsx"]).toContain('"/about": AboutPage');
    expect(JSON.parse(byPath["package.json"]).devDependencies.vite).toBeDefined();
  });

  it("moves fonts, favicon and base styles into the layout", () => {
    const head = {
      lang: "ru",
      description: 'Say "hi"',
      favicon: "http://127.0.0.1:7001/crawl-assets/0123456789abcdef0123.ico",
      fontLinks: ["https://fonts.googleapis.com/css2?family=Inter"],
      baseCss: "body { font-family: 'Inter', sans-serif; }",
    };
    const withHead = (html: string) =>
      html.replace(
        "</head>",
        `<script type="application/json" id="urltocode-head">${JSON.stringify(head)}</script></head>`
      );

    const next = buildFrameworkProjectFiles(
      { "/": withHead(preview("nextjs", "/", HOME)) },
      "Site"
    )!;
    const nextByPath = Object.fromEntries(next.map((f) => [f.path, f.content]));
    expect(nextByPath["app/layout.tsx"]).toContain('<html lang="ru">');
    expect(nextByPath["app/layout.tsx"]).toContain('description: "Say \\"hi\\""');
    expect(nextByPath["app/layout.tsx"]).toContain(`icons: { icon: "${head.favicon}" }`);
    expect(nextByPath["app/layout.tsx"]).toContain(
      '<link rel="stylesheet" href={"https://fonts.googleapis.com/css2?family=Inter"} />'
    );
    expect(nextByPath["app/globals.css"]).toContain(head.baseCss);

    const react = buildFrameworkProjectFiles(
      { "/": withHead(preview("react", "/", HOME)) },
      "Site"
    )!;
    const reactByPath = Object.fromEntries(react.map((f) => [f.path, f.content]));
    expect(reactByPath["index.html"]).toContain('<html lang="ru">');
    expect(reactByPath["index.html"]).toContain('content="Say &quot;hi&quot;"');
    expect(reactByPath["index.html"]).toContain(`<link rel="icon" href="${head.favicon}" />`);
    expect(reactByPath["src/index.css"]).toContain(head.baseCss);
  });

  it("returns null for plain HTML clones", () => {
    expect(buildFrameworkProjectFiles({ "/": "<!DOCTYPE html><html></html>" }, "S")).toBeNull();
  });
});

describe("editing a framework project", () => {
  const nextCode = {
    "/": preview("nextjs", "/", HOME),
    "/about": preview("nextjs", "/about", ABOUT),
  };
  const reactCode = {
    "/": preview("react", "/", HOME),
    "/about": preview("react", "/about", ABOUT),
  };

  it("maps each page to the project file that holds it", () => {
    const nextMap = frameworkPageFileMap(nextCode)!;
    expect(nextMap.get("/")).toBe("app/page.tsx");
    expect(nextMap.get("/about")).toBe("app/about/page.tsx");

    const reactMap = frameworkPageFileMap(reactCode)!;
    expect(reactMap.get("/")).toBe("src/pages/HomePage.tsx");
    expect(reactMap.get("/about")).toBe("src/pages/AboutPage.tsx");
  });

  it("returns no map for plain HTML clones", () => {
    expect(frameworkPageFileMap({ "/": "<!DOCTYPE html><html></html>" })).toBeNull();
  });

  it("rebuilds the preview document from an edited page component", () => {
    const edited = `"use client";\nexport default function Page() {\n  return <main>fixed</main>;\n}`;
    const rebuilt = rebuildPreviewSource(nextCode["/about"], edited);

    expect(parseFrameworkPage(rebuilt)?.source).toBe(edited);
    // The wrapper and its runtime must survive the edit.
    expect(rebuilt).toContain("/* runtime */");
    expect(rebuilt).toContain("<title>Site</title>");
  });

  it("re-escapes a closing script tag inside edited source", () => {
    const edited = `"use client";\nexport default function Page() {\n  return <main>{"</script>"}</main>;\n}`;
    const rebuilt = rebuildPreviewSource(nextCode["/"], edited);

    // The tag must not have been closed early by the component's own markup.
    expect(rebuilt).not.toContain('{"</script>"}');
    expect(parseFrameworkPage(rebuilt)?.source).toBe(edited);
  });

  it("leaves a preview without an embedded source untouched", () => {
    const plain = "<!DOCTYPE html><html><body>hi</body></html>";
    expect(rebuildPreviewSource(plain, "anything")).toBe(plain);
  });

  it("applies an edited file to the project, and keeps unknown paths", () => {
    const files = buildFrameworkProjectFiles(nextCode, "Site")!;
    const applied = applyFileOverrides(files, {
      "app/about/page.tsx": "EDITED",
      "src/extra.ts": "NEW FILE",
    });
    const byPath = Object.fromEntries(applied.map((f) => [f.path, f.content]));

    expect(byPath["app/about/page.tsx"]).toBe("EDITED");
    expect(byPath["app/page.tsx"]).toBe(`${HOME}\n`);
    expect(byPath["src/extra.ts"]).toBe("NEW FILE");
  });

  it("does not copy the array when there is nothing to apply", () => {
    const files = buildFrameworkProjectFiles(nextCode, "Site")!;
    expect(applyFileOverrides(files, {})).toBe(files);
  });
});
