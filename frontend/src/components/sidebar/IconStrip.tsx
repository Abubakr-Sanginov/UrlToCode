import { LuClock, LuCode, LuSettings, LuPlus } from "react-icons/lu";

interface IconStripProps {
  isHistoryOpen: boolean;
  isEditorOpen: boolean;
  isSettingsOpen: boolean;
  showHistory: boolean;
  showEditor: boolean;
  onToggleHistory: () => void;
  onToggleEditor: () => void;
  onLogoClick: () => void;
  onNewProject: () => void;
  onOpenSettings: () => void;
}

const NAV_ITEM =
  "flex items-center justify-center rounded-lg p-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:flex-col lg:gap-1 lg:px-2 lg:py-1.5";
const NAV_ACTIVE = "bg-accent text-foreground";
const NAV_IDLE = "text-muted-foreground hover:bg-accent/60 hover:text-foreground";

function IconStrip({
  isHistoryOpen,
  isEditorOpen,
  isSettingsOpen,
  showHistory,
  showEditor,
  onToggleHistory,
  onToggleEditor,
  onLogoClick,
  onNewProject,
  onOpenSettings,
}: IconStripProps) {
  return (
    <nav
      aria-label="Main"
      className="flex w-full items-center justify-between border-b border-border bg-card px-2 py-2 lg:h-full lg:w-16 lg:flex-col lg:items-center lg:gap-y-2 lg:border-b-0 lg:border-r lg:px-0 lg:py-4"
    >
      <button
        type="button"
        onClick={onLogoClick}
        aria-label="Go to editor"
        title="Go to editor"
        className="rounded-lg p-2 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:mb-1 lg:p-1.5"
      >
        <img src="/favicon/main.png" alt="" className="h-5 w-5 dark:invert" />
      </button>

      <div className="flex items-center gap-1 lg:flex-col lg:gap-0 lg:contents">
        {showEditor && (
          <button
            type="button"
            onClick={onToggleEditor}
            aria-label="Editor"
            aria-current={isEditorOpen ? "page" : undefined}
            className={`${NAV_ITEM} ${isEditorOpen ? NAV_ACTIVE : NAV_IDLE}`}
            title="Editor"
          >
            <LuCode className="h-[18px] w-[18px]" aria-hidden="true" />
            <span className="hidden text-[10px] leading-none lg:block">
              Editor
            </span>
          </button>
        )}

        {showHistory && (
          <button
            type="button"
            onClick={onToggleHistory}
            aria-label="Versions"
            aria-current={isHistoryOpen ? "page" : undefined}
            className={`${NAV_ITEM} ${isHistoryOpen ? NAV_ACTIVE : NAV_IDLE}`}
            title="Versions"
          >
            <LuClock className="h-[18px] w-[18px]" aria-hidden="true" />
            <span className="hidden text-[10px] leading-none lg:block">
              Versions
            </span>
          </button>
        )}

        <button
          type="button"
          onClick={onNewProject}
          aria-label="Start a new project"
          className={`${NAV_ITEM} border border-border bg-background text-foreground hover:bg-accent`}
          title="Start a new project"
        >
          <LuPlus className="h-[18px] w-[18px]" aria-hidden="true" />
          <span className="hidden text-[10px] font-medium leading-none lg:block">
            New
          </span>
        </button>
      </div>

      {/* Spacer pushes settings to the bottom on desktop */}
      <div className="hidden flex-1 lg:block" />

      <button
        type="button"
        onClick={onOpenSettings}
        aria-label="Settings"
        aria-current={isSettingsOpen ? "page" : undefined}
        className={`${NAV_ITEM} ${isSettingsOpen ? NAV_ACTIVE : NAV_IDLE}`}
        title="Settings"
      >
        <LuSettings className="h-[18px] w-[18px]" aria-hidden="true" />
        <span className="hidden text-[10px] leading-none lg:block">Settings</span>
      </button>
    </nav>
  );
}

export default IconStrip;
