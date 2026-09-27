import React from "react";
import { Settings } from "../../types";
import { Stack } from "../../lib/stacks";
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

const StartPane: React.FC<Props> = ({
  startCrawl,
  cancelCrawl,
  getState,
  subscribe,
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
    <div className="flex flex-col justify-center items-center py-8">
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
};

export default StartPane;
