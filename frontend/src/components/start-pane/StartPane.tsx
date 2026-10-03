import React from "react";
import { Settings } from "../../types";
import { CloneStack, NEXTJS_STACK, Stack, cloneStackLabel } from "../../lib/stacks";
import UrlToCodePane from "../url-to-code/UrlToCodePane";
import { CrawlRunState, StartCrawlParams } from "../../hooks/useUrlToCode";
import {
  LuGlobe2,
  LuFolderOpen,
  LuSparkles,
} from "react-icons/lu";

interface Props {
  startCrawl: (params: StartCrawlParams) => void;
  cancelCrawl: () => void;
  choosePages: (paths: string[]) => void;
  state: CrawlRunState;
  settings: Settings;
}

const FEATURES = [
  {
    icon: LuGlobe2,
    title: "Paste a URL",
    description: "The crawler maps the site and reads every page",
  },
  {
    icon: LuSparkles,
    title: "AI clones the code",
    description: "Each page is rebuilt as clean, editable code",
  },
  {
    icon: LuFolderOpen,
    title: "Open folder & run",
    description: "Files land in your folder and the site starts instantly",
  },
] as const;

const stackNames: string[] = ([NEXTJS_STACK, ...Object.values(Stack)] as CloneStack[])
  .slice(0, 6)
  .map((stack) => cloneStackLabel(stack));

const StartPane: React.FC<Props> = ({
  startCrawl,
  cancelCrawl,
  choosePages,
  state,
  settings,
}) => {
  return (
    <div className="relative flex flex-1 flex-col items-center overflow-y-auto px-4 py-10 sm:py-14">
      {/* Ambient background: a faint green terminal glow from the top */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-[radial-gradient(ellipse_at_top,hsl(var(--brand)/0.07),transparent_65%)]"
      />

      <div className="relative flex w-full max-w-2xl flex-col items-center">
        {/* Hero */}
        <div className="flex flex-col items-center text-center">
          <p
            aria-hidden="true"
            className="mb-4 font-mono text-sm text-brand"
          >
            <span className="text-muted-foreground">$</span> url --to-code
            <span className="caret-block" />
          </p>
          <h1 className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
            Clone any website
          </h1>
          <p className="mt-3 max-w-md text-sm leading-6 text-muted-foreground sm:text-base">
            Paste a link and get a working local copy — generated file by file,
            saved to your folder and running in one click.
          </p>
        </div>

        {/* Clone card */}
        <div className="mt-8 w-full">
          <UrlToCodePane
            startCrawl={startCrawl}
            cancelCrawl={cancelCrawl}
            choosePages={choosePages}
            state={state}
            settings={settings}
          />
        </div>

        {/* How it works */}
        <div className="mt-10 grid w-full gap-3 sm:grid-cols-3">
          {FEATURES.map((feature, index) => (
            <div
              key={feature.title}
              className="rounded-lg border border-border bg-card p-4"
            >
              <div className="flex items-center justify-between">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border bg-canvas text-muted-foreground">
                  <feature.icon className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span className="font-mono text-xs tabular-nums text-brand">
                  0{index + 1}
                </span>
              </div>
              <h3 className="mt-2.5 text-sm font-semibold text-foreground">
                {feature.title}
              </h3>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {feature.description}
              </p>
            </div>
          ))}
        </div>

        {/* Stack chips */}
        <div className="mt-8 flex flex-wrap items-center justify-center gap-1.5">
          {stackNames.map((name) => (
            <span
              key={name}
              className="rounded border border-border bg-card px-2 py-0.5 font-mono text-[11px] text-muted-foreground"
            >
              {name}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
};

export default StartPane;
