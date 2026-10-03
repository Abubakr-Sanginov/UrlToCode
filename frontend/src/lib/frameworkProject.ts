import type { ProjectFile } from "./localProject";

/**
 * Framework clones (Next.js, React + Vite).
 *
 * The backend generates one TSX page component per crawled page and ships it
 * inside a self-rendering preview document, so the preview pane and commits
 * keep working on HTML. The component source sits in a
 * `<script type="text/plain" id="urltocode-source">` tag
 * (backend/prompts/framework_stacks.py); this module pulls it back out and
 * lays the pages out as a real project around it.
 */

export type Framework = "nextjs" | "react";

/** Site-wide head settings (backend/prompts/framework_stacks.py `build_head`). */
export interface SiteHead {
  lang: string;
  description: string;
  favicon: string;
  fontLinks: string[];
  baseCss: string;
}

export interface FrameworkPage {
  framework: Framework;
  route: string;
  source: string;
  previewHtml: string;
  head: SiteHead | null;
}

const HEAD_TAG_RE =
  /<script type="application\/json" id="urltocode-head">([\s\S]*?)<\/script>/i;

const EMPTY_HEAD: SiteHead = {
  lang: "",
  description: "",
  favicon: "",
  fontLinks: [],
  baseCss: "",
};

/** The head settings a preview document carries, or null when it has none. */
function parseSiteHead(html: string): SiteHead | null {
  const match = html.match(HEAD_TAG_RE);
  if (!match) return null;
  try {
    const raw = JSON.parse(match[1].replace(/<\\\//g, "</")) as Partial<SiteHead>;
    return {
      lang: typeof raw.lang === "string" ? raw.lang : "",
      description: typeof raw.description === "string" ? raw.description : "",
      favicon: typeof raw.favicon === "string" ? raw.favicon : "",
      fontLinks: Array.isArray(raw.fontLinks)
        ? raw.fontLinks.filter((link): link is string => typeof link === "string")
        : [],
      baseCss: typeof raw.baseCss === "string" ? raw.baseCss : "",
    };
  } catch {
    return null;
  }
}

function escapeAttribute(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function globalCss(head: SiteHead): string {
  return head.baseCss ? `${TAILWIND_DIRECTIVES}\n${head.baseCss}\n` : TAILWIND_DIRECTIVES;
}

const SOURCE_TAG_RE =
  /<script type="text\/plain" id="urltocode-source" data-framework="(nextjs|react)" data-route="([^"]*)">([\s\S]*?)<\/script>/i;

function decodeAttribute(value: string): string {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

/** The embedded page component, or null for a plain HTML page. */
export function parseFrameworkPage(html: string): FrameworkPage | null {
  const match = html.match(SOURCE_TAG_RE);
  if (!match) return null;
  return {
    framework: match[1].toLowerCase() as Framework,
    route: decodeAttribute(match[2]) || "/",
    // The backend escapes "</script" so the tag cannot end early.
    source: match[3].replace(/<\\\/(script)/gi, "</$1"),
    previewHtml: html,
    head: parseSiteHead(html),
  };
}

/** Framework of a generated code map, or null when it is plain HTML. */
export function detectFramework(code: Record<string, string>): Framework | null {
  for (const value of Object.values(code)) {
    const page = value ? parseFrameworkPage(value) : null;
    if (page) return page.framework;
  }
  return null;
}

/** Mirrors `route_for_path` in backend/prompts/framework_stacks.py. */
export function routeForPath(path: string): string {
  const cleaned = (path || "").split("?")[0].split("#")[0];
  const segments = cleaned
    .split("/")
    .map((segment) =>
      segment
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
    )
    .filter(Boolean);
  return "/" + segments.join("/");
}

/** Static preview file for a route; the preview runtime links by this name. */
function previewFileForRoute(route: string): string {
  const slug = route
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${slug || "index"}.html`;
}

function componentName(route: string): string {
  const name = route
    .split(/[^a-zA-Z0-9]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1))
    .join("");
  if (!name) return "HomePage";
  return /^[0-9]/.test(name) ? `Page${name}` : `${name}Page`;
}

/** Project file that holds the page component for `route` in a Next.js project. */
function nextjsPageFilePath(route: string): string {
  const dir = route === "/" ? "app" : `app${route}`;
  return `${dir}/page.tsx`;
}

/**
 * Component file name per route in a React + Vite project. Two routes can
 * normalise to the same name ("/a-b" and "/a_b"), so collisions get a counter.
 */
function reactPageFileNames(routes: string[]): Map<string, string> {
  const names = new Map<string, string>();
  const used = new Set<string>();
  for (const route of routes) {
    let name = componentName(route);
    for (let n = 2; used.has(name); n++) name = `${componentName(route)}${n}`;
    used.add(name);
    names.set(route, name);
  }
  return names;
}

function packageName(siteName: string): string {
  const slug = siteName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug.slice(0, 60) || "cloned-site";
}

function json(value: unknown): string {
  return JSON.stringify(value, null, 2) + "\n";
}

const TAILWIND_DIRECTIVES = `@tailwind base;
@tailwind components;
@tailwind utilities;
`;

const POSTCSS_CONFIG = `export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
`;

const GITIGNORE = `node_modules
.next
dist
out
*.tsbuildinfo
next-env.d.ts
`;

function tailwindConfig(content: string[]): string {
  return `import type { Config } from "tailwindcss";

const config: Config = {
  content: ${JSON.stringify(content)},
  theme: { extend: {} },
  plugins: [],
};

export default config;
`;
}

function escapeForJsString(value: string): string {
  return JSON.stringify(value);
}

function nextjsLayout(siteName: string, head: SiteHead): string {
  const metadata = [`  title: ${escapeForJsString(siteName)},`];
  if (head.description) metadata.push(`  description: ${escapeForJsString(head.description)},`);
  if (head.favicon) metadata.push(`  icons: { icon: ${escapeForJsString(head.favicon)} },`);
  const fontTags = head.fontLinks
    .map((href) => `        <link rel="stylesheet" href={${escapeForJsString(href)}} />`)
    .join("\n");
  const headBlock = fontTags ? `\n      <head>\n${fontTags}\n      </head>` : "";

  return `import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
${metadata.join("\n")}
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang=${escapeForJsString(head.lang || "en")}>${headBlock}
      <body>{children}</body>
    </html>
  );
}
`;
}

function viteIndexHtml(siteName: string, head: SiteHead): string {
  const tags = [
    `    <title>${siteName.replace(/</g, "&lt;")}</title>`,
    head.description
      ? `    <meta name="description" content="${escapeAttribute(head.description)}" />`
      : "",
    head.favicon ? `    <link rel="icon" href="${escapeAttribute(head.favicon)}" />` : "",
    ...head.fontLinks.map(
      (href) => `    <link rel="stylesheet" href="${escapeAttribute(href)}" />`
    ),
  ].filter(Boolean);
  return `<!DOCTYPE html>
<html lang="${escapeAttribute(head.lang || "en")}">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
${tags.join("\n")}
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
`;
}

function nextjsFiles(
  pages: FrameworkPage[],
  siteName: string,
  head: SiteHead
): ProjectFile[] {
  const files: ProjectFile[] = [
    {
      path: "package.json",
      content: json({
        name: packageName(siteName),
        version: "0.1.0",
        private: true,
        scripts: { dev: "next dev", build: "next build", start: "next start" },
        dependencies: { next: "14.2.15", react: "^18.3.1", "react-dom": "^18.3.1" },
        devDependencies: {
          "@types/node": "^20",
          "@types/react": "^18",
          "@types/react-dom": "^18",
          autoprefixer: "^10.4.20",
          postcss: "^8.4.47",
          tailwindcss: "^3.4.14",
          typescript: "^5",
        },
      }),
    },
    {
      path: "next.config.mjs",
      // Generated pages are model output: a stray type error must not stop
      // the clone from building.
      content: `/** @type {import('next').NextConfig} */
const nextConfig = {
  images: { unoptimized: true },
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },
};

export default nextConfig;
`,
    },
    {
      path: "tsconfig.json",
      content: json({
        compilerOptions: {
          target: "ES2017",
          lib: ["dom", "dom.iterable", "esnext"],
          allowJs: true,
          skipLibCheck: true,
          strict: false,
          noEmit: true,
          esModuleInterop: true,
          module: "esnext",
          moduleResolution: "bundler",
          resolveJsonModule: true,
          isolatedModules: true,
          jsx: "preserve",
          incremental: true,
          plugins: [{ name: "next" }],
          paths: { "@/*": ["./*"] },
        },
        include: ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
        exclude: ["node_modules"],
      }),
    },
    { path: "postcss.config.mjs", content: POSTCSS_CONFIG },
    {
      path: "tailwind.config.ts",
      content: tailwindConfig(["./app/**/*.{ts,tsx}"]),
    },
    { path: "app/globals.css", content: globalCss(head) },
    { path: "app/layout.tsx", content: nextjsLayout(siteName, head) },
  ];

  for (const page of pages) {
    files.push({
      path: nextjsPageFilePath(page.route),
      content: ensureTrailingNewline(page.source),
    });
  }
  return files;
}

function reactFiles(
  pages: FrameworkPage[],
  siteName: string,
  head: SiteHead
): ProjectFile[] {
  const names = reactPageFileNames(pages.map((page) => page.route));

  const imports = pages
    .map((page) => `import ${names.get(page.route)} from "./pages/${names.get(page.route)}";`)
    .join("\n");
  const routes = pages
    .map((page) => `  ${escapeForJsString(page.route)}: ${names.get(page.route)},`)
    .join("\n");
  const fallback = names.get("/") ?? names.get(pages[0].route);

  const files: ProjectFile[] = [
    {
      path: "package.json",
      content: json({
        name: packageName(siteName),
        private: true,
        version: "0.1.0",
        type: "module",
        scripts: { dev: "vite", build: "vite build", preview: "vite preview" },
        dependencies: { react: "^18.3.1", "react-dom": "^18.3.1" },
        devDependencies: {
          "@types/react": "^18",
          "@types/react-dom": "^18",
          "@vitejs/plugin-react": "^4.3.3",
          autoprefixer: "^10.4.20",
          postcss: "^8.4.47",
          tailwindcss: "^3.4.14",
          typescript: "^5",
          vite: "^5.4.10",
        },
      }),
    },
    {
      path: "vite.config.ts",
      content: `import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
});
`,
    },
    {
      path: "tsconfig.json",
      content: json({
        compilerOptions: {
          target: "ES2020",
          lib: ["ES2020", "DOM", "DOM.Iterable"],
          module: "ESNext",
          moduleResolution: "bundler",
          jsx: "react-jsx",
          strict: false,
          skipLibCheck: true,
          noEmit: true,
          isolatedModules: true,
        },
        include: ["src"],
      }),
    },
    { path: "postcss.config.js", content: POSTCSS_CONFIG },
    {
      path: "tailwind.config.ts",
      content: tailwindConfig(["./index.html", "./src/**/*.{ts,tsx}"]),
    },
    { path: "index.html", content: viteIndexHtml(siteName, head) },
    { path: "src/index.css", content: globalCss(head) },
    {
      path: "src/main.tsx",
      // Pages link with plain <a href="/route">, so the pathname picks the
      // page; Vite's dev server answers every route with index.html.
      content: `import React from "react";
import ReactDOM from "react-dom/client";
import "./index.css";
${imports}

const routes: Record<string, React.ComponentType> = {
${routes}
};

const path = window.location.pathname.replace(/\\/+$/, "") || "/";
const Page = routes[path] ?? ${fallback};

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Page />
  </React.StrictMode>
);
`,
    },
  ];

  for (const page of pages) {
    files.push({
      path: `src/pages/${names.get(page.route)}.tsx`,
      content: ensureTrailingNewline(page.source),
    });
  }
  return files;
}

function ensureTrailingNewline(text: string): string {
  return text.endsWith("\n") ? text : `${text}\n`;
}

function readme(framework: Framework, siteName: string, fileNames: string[]): string {
  const runner =
    framework === "nextjs"
      ? "Next.js 14 (App Router) + TypeScript + Tailwind CSS"
      : "React 18 + Vite + TypeScript + Tailwind CSS";
  const list = fileNames.map((name) => `- \`${name}\``).join("\n");
  return `# ${siteName}

Generated by UrlToCode — ${runner}.

## Run locally

\`\`\`bash
npm install
npm run dev
\`\`\`

\`preview/\` holds a static copy of every page that opens without installing
anything (\`preview/index.html\`).

## Files

${list}
`;
}

/**
 * Lays framework pages out as a runnable project. Returns null when the code
 * map holds plain HTML pages.
 */
export function buildFrameworkProjectFiles(
  code: Record<string, string>,
  siteName: string
): ProjectFile[] | null {
  const pages: FrameworkPage[] = [];
  const seenRoutes = new Set<string>();
  for (const [key, value] of Object.entries(code)) {
    if (!value || key === "database-schema") continue;
    const page = parseFrameworkPage(value);
    if (!page || seenRoutes.has(page.route)) continue;
    seenRoutes.add(page.route);
    pages.push(page);
  }
  if (pages.length === 0) return null;

  // Every page shares the framework and head settings of the run.
  const framework = pages[0].framework;
  const head =
    pages.find((page) => page.route === "/")?.head ??
    pages.find((page) => page.head)?.head ??
    EMPTY_HEAD;
  const files =
    framework === "nextjs"
      ? nextjsFiles(pages, siteName, head)
      : reactFiles(pages, siteName, head);

  files.push({ path: ".gitignore", content: GITIGNORE });
  for (const page of pages) {
    files.push({
      path: `preview/${previewFileForRoute(page.route)}`,
      content: page.previewHtml,
    });
  }
  if (code["database-schema"]?.trim()) {
    files.push({ path: "database-schema.sql", content: code["database-schema"] });
  }
  files.push({
    path: "README.md",
    content: readme(framework, siteName, files.map((file) => file.path)),
  });
  return files;
}

/** Pages of a code map, in the order `buildFrameworkProjectFiles` lays them out. */
function frameworkPagesOf(code: Record<string, string>): FrameworkPage[] {
  const pages: FrameworkPage[] = [];
  const seenRoutes = new Set<string>();
  for (const [key, value] of Object.entries(code)) {
    if (!value || key === "database-schema") continue;
    const page = parseFrameworkPage(value);
    if (!page || seenRoutes.has(page.route)) continue;
    seenRoutes.add(page.route);
    pages.push(page);
  }
  return pages;
}

/**
 * Page path (the key used in the clone's code map) -> the project file that
 * holds that page's component.
 *
 * The editor works in project-file paths, while the preview renders the
 * self-contained document a page was generated as. This map is what lets an
 * edit to `app/pricing/page.tsx` reach the page it belongs to. Returns null
 * when the code map holds plain HTML pages.
 */
export function frameworkPageFileMap(
  code: Record<string, string>
): Map<string, string> | null {
  const pages = frameworkPagesOf(code);
  if (pages.length === 0) return null;

  const map = new Map<string, string>();
  if (pages[0].framework === "nextjs") {
    for (const page of pages) {
      map.set(page.route, nextjsPageFilePath(page.route));
    }
    return map;
  }
  const names = reactPageFileNames(pages.map((page) => page.route));
  for (const page of pages) {
    const name = names.get(page.route);
    if (name) map.set(page.route, `src/pages/${name}.tsx`);
  }
  return map;
}

/**
 * Put edited file contents back into a generated preview document, so the
 * preview shows the editor's version of a page component.
 */
export function rebuildPreviewSource(
  previewHtml: string,
  source: string
): string {
  const match = SOURCE_TAG_RE.exec(previewHtml);
  if (!match) return previewHtml;
  // The backend escapes "</script" so the tag cannot be closed early by the
  // component's own markup; the same escape has to hold here.
  const escaped = source.replace(/<\/script/gi, "<\\/script");
  const rebuilt = `<script type="text/plain" id="urltocode-source" data-framework="${match[1]}" data-route="${match[2]}">${escaped}</script>`;
  // A function replacer: component code routinely contains `$&`-like text that
  // a string replacement would expand.
  return previewHtml.replace(SOURCE_TAG_RE, () => rebuilt);
}

/**
 * Apply edited file contents to a laid-out project. Overrides for files the
 * layout does not produce are appended, so a hand-added file survives export.
 */
export function applyFileOverrides(
  files: ProjectFile[],
  overrides: Record<string, string>
): ProjectFile[] {
  const paths = Object.keys(overrides);
  if (paths.length === 0) return files;

  const seen = new Set<string>();
  const merged = files.map((file) => {
    seen.add(file.path);
    const override = overrides[file.path];
    return override === undefined ? file : { ...file, content: override };
  });
  for (const path of paths) {
    if (!seen.has(path)) merged.push({ path, content: overrides[path] });
  }
  return merged;
}
