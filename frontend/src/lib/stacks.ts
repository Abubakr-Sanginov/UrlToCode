// Keep in sync with backend (prompts/types.py)
// Order here determines order in dropdown
export enum Stack {
  HTML_TAILWIND = "html_tailwind",
  HTML_CSS = "html_css",
  REACT_TAILWIND = "react_tailwind",
  BOOTSTRAP = "bootstrap",
  VUE_TAILWIND = "vue_tailwind",
  IONIC_TAILWIND = "ionic_tailwind",
}

export const STACK_DESCRIPTIONS: {
  [key in Stack]: { components: string[]; inBeta: boolean };
} = {
  html_css: { components: ["HTML", "CSS"], inBeta: false },
  html_tailwind: { components: ["HTML", "Tailwind"], inBeta: false },
  react_tailwind: { components: ["React", "Tailwind"], inBeta: false },
  bootstrap: { components: ["Bootstrap"], inBeta: false },
  vue_tailwind: { components: ["Vue", "Tailwind"], inBeta: true },
  ionic_tailwind: { components: ["Ionic", "Tailwind"], inBeta: true },
};

// URL cloning only: a real Next.js project, one page component per route.
// The screenshot flow has no Next.js output, so it is not part of `Stack`.
export const NEXTJS_STACK = "nextjs_tailwind";
export type CloneStack = Stack | typeof NEXTJS_STACK;

export function cloneStackLabel(stack: CloneStack): string {
  if (stack === NEXTJS_STACK) return "Next.js + Tailwind";
  if (stack === Stack.REACT_TAILWIND) return "React + Vite + Tailwind";
  return STACK_DESCRIPTIONS[stack].components.join(" + ");
}

/** Stacks whose clone is a framework project rather than HTML files. */
export function isFrameworkStack(stack: CloneStack): boolean {
  return stack === NEXTJS_STACK || stack === Stack.REACT_TAILWIND;
}
