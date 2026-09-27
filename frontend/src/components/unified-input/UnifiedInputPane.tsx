import React from "react";
import { Stack } from "../../lib/stacks";
import { Settings } from "../../types";
import UrlToCodePane from "../url-to-code/UrlToCodePane";

interface Props {
  startCrawl: (
    url: string,
    stack: string,
    maxPages: number,
    maxDepth: number,
    generateDatabase: boolean,
    generateAuth: boolean,
    settings: Settings
  ) => void;
  cancelCrawl: () => void;
  getState: () => any;
  subscribe: (fn: () => void) => () => void;
  settings: Settings;
  setSettings: React.Dispatch<React.SetStateAction<Settings>>;
}

function UnifiedInputPane({
  startCrawl,
  cancelCrawl,
  getState,
  subscribe,
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
    <div className="w-full max-w-4xl mx-auto px-4">
      <UrlToCodePane
        stack={settings.generatedCodeConfig}
        setStack={setStack}
        startCrawl={startCrawl}
        cancelCrawl={cancelCrawl}
        getState={getState}
        subscribe={subscribe}
        settings={settings}
      />
    </div>
  );
}

export default UnifiedInputPane;
