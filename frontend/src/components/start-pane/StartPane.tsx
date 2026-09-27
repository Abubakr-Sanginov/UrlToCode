import React from "react";
import { Settings } from "../../types";
import { Stack, STACK_DESCRIPTIONS } from "../../lib/stacks";
import UrlToCodePane from "../url-to-code/UrlToCodePane";
import { CrawlRunState, StartCrawlParams } from "../../hooks/useUrlToCode";
import {
  LuGlobe2,
  LuFolderOpen,
  LuSparkles,
  LuZap,
} from "react-icons/lu";

interface Props {
  startCrawl: (params: StartCrawlParams) => void;
  cancelCrawl: () => void;
  state: CrawlRunState;
  settings: Settings;
  setSettings: React.Dispatch<React.SetStateAction<Settings>>;
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

const stackNames = Object.values(Stack)
  .slice(0, 5)
  .map((stack) => STACK_DESCRIPTIONS[stack].components.join(" + "));

const StartPane: React.FC<Props> = ({
  startCrawl,
  cancelCrawl,
  state,
  settings,
  setSettings,
}) => {
  function setStack(stack: Stack) {
    setSettings((prev: Settings) => ({
      ...prev,
      generatedCodeConfig: stack,
    }));
  }

  return (
    <div className="relative flex flex-1 flex-col items-center overflow-y-auto px-4 py-10 sm:py-14">
      {/* Ambient background glow */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 h-[420px] bg-[radial-gradient(ellipse_at_top,hsl(var(--brand)/0.10),transparent_65%)]"
      />

      <div className="relative flex w-full max-w-2xl flex-col items-center">
        {/* Hero */}
        <div className="flex flex-col items-center text-center">
          <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-brand-border bg-brand-subtle px-3 py-1 text-xs font-medium text-brand">
            <LuZap className="h-3 w-3" aria-hidden="true" />
            URL → code, fully automatic
          </div>
          <h1 className="bg-gradient-to-br from-foreground via-foreground to-brand bg-clip-text text-4xl font-bold tracking-tight text-transparent sm:text-5xl">
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
            stack={settings.generatedCodeConfig}
            setStack={setStack}
            startCrawl={startCrawl}
            cancelCrawl={cancelCrawl}
            state={state}
            settings={settings}
          />
        </div>

        {/* How it works */}
        <div className="mt-10 grid w-full gap-3 sm:grid-cols-3">
          {FEATURES.map((feature, index) => (
            <div
              key={feature.title}
              className="rounded-xl border border-border bg-card/60 p-4 shadow-card backdrop-blur-sm"
            >
              <div className="flex items-center gap-2.5">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-brand-subtle text-brand">
                  <feature.icon className="h-3.5 w-3.5" aria-hidden="true" />
                </span>
                <span className="text-xs font-semibold tabular-nums text-muted-foreground/60">
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
          <span className="text-[11px] uppercase tracking-wider text-muted-foreground/70">
            Stacks
          </span>
          {stackNames.map((name) => (
            <span
              key={name}
              className="rounded-full border border-border bg-card px-2.5 py-0.5 text-[11px] text-muted-foreground"
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
