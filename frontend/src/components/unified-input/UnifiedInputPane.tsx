import React from "react";
import { Stack } from "../../lib/stacks";
import { Settings } from "../../types";
import UrlToCodePane from "../url-to-code/UrlToCodePane";
import { CrawlRunState, StartCrawlParams } from "../../hooks/useUrlToCode";

interface Props {
  startCrawl: (params: StartCrawlParams) => void;
  cancelCrawl: () => void;
  state: CrawlRunState;
  settings: Settings;
  setSettings: React.Dispatch<React.SetStateAction<Settings>>;
}

function UnifiedInputPane({
  startCrawl,
  cancelCrawl,
  state,
  settings,
  setSettings,
}: Props) {
  function setStack(stack: Stack) {
    setSettings((prev: Settings) => ({
      ...prev,
      generatedCodeConfig: stack,
    }));
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-4">
      <UrlToCodePane
        stack={settings.generatedCodeConfig}
        setStack={setStack}
        startCrawl={startCrawl}
        cancelCrawl={cancelCrawl}
        state={state}
        settings={settings}
      />
    </div>
  );
}

export default UnifiedInputPane;
