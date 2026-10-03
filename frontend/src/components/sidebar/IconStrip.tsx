import { LuClock, LuCode, LuSettings, LuPlus, LuFolder } from "react-icons/lu";
import { AccountNavItem, AccountPanel } from "./AccountNavItem";
import { useAccountUi } from "../../store/account-ui-store";
import { useEffect, useRef } from "react";

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
  isProjectsOpen: boolean;
  onToggleProjects: () => void;
}

const NAV_ITEM =
  "relative flex items-center justify-center rounded-md p-2 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:flex-col lg:gap-1 lg:px-2 lg:py-1.5";
const NAV_ACTIVE =
  "bg-brand-subtle text-brand after:absolute after:left-0 after:top-1/2 after:hidden after:h-5 after:w-0.5 after:-translate-y-1/2 after:rounded-full after:bg-brand after:content-[''] lg:after:block";
const NAV_IDLE = "text-muted-foreground hover:bg-accent/60 hover:text-foreground";

function IconStrip({
  isHistoryOpen,
  isEditorOpen,
  isSettingsOpen,
  showHistory,
  showEditor,
  isProjectsOpen,
  onToggleHistory,
  onToggleEditor,
  onToggleProjects,
  onLogoClick,
  onNewProject,
  onOpenSettings,
}: IconStripProps) {
  const isAccountPanelOpen = useAccountUi((state) => state.isPanelOpen);
  const closePanel = useAccountUi((state) => state.closePanel);
  const accountRef = useRef<HTMLDivElement>(null);

  // A click anywhere else closes the popover. A panel that stays open over
  // the editor hides the work behind it. The click that opened it must not
  // also close it, so a press on the avatar itself is left alone.
  useEffect(() => {
    if (!isAccountPanelOpen) return;
    const close = (event: MouseEvent) => {
      if (accountRef.current?.contains(event.target as Node)) return;
      closePanel();
    };
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [isAccountPanelOpen, closePanel]);

  return (
    <nav
      aria-label="Main"
      className="flex w-full items-center justify-between border-b border-border bg-canvas px-2 py-2 lg:h-full lg:w-16 lg:flex-col lg:items-center lg:gap-y-2 lg:border-b-0 lg:border-r lg:px-0 lg:py-4"
    >
      <button
        type="button"
        onClick={onLogoClick}
        aria-label="Go to editor"
        title="Go to editor"
        data-testid="go-to-editor"
        className="rounded-md p-2 transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:mb-1 lg:p-1.5"
      >
        <img src="/favicon/main.svg" alt="" className="h-6 w-6" />
      </button>

      <div className="flex items-center gap-1 lg:flex-col lg:gap-0 lg:contents">
        {/* Always there, signed out or not: an account's projects are
            theirs, and the count in the panel is useless without a way to
            get to them. A page, not a popover - it is where someone goes to
            find something they made earlier. */}
        <button
          type="button"
          onClick={onToggleProjects}
          aria-label="Projects"
          aria-current={isProjectsOpen ? "page" : undefined}
          className={`${NAV_ITEM} ${isProjectsOpen ? NAV_ACTIVE : NAV_IDLE}`}
          title="Your projects"
          data-testid="projects-nav"
        >
          <LuFolder className="h-[18px] w-[18px]" aria-hidden="true" />
          <span className="hidden font-mono text-[10px] leading-none lg:block">
            Projects
          </span>
        </button>

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
            <span className="hidden font-mono text-[10px] leading-none lg:block">
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
            <span className="hidden font-mono text-[10px] leading-none lg:block">
              Versions
            </span>
          </button>
        )}

        <button
          type="button"
          onClick={onNewProject}
          aria-label="Start a new project"
          data-testid="new-project"
          className={`${NAV_ITEM} border border-dashed border-input bg-transparent text-foreground hover:border-brand hover:text-brand`}
          title="Start a new project"
        >
          <LuPlus className="h-[18px] w-[18px]" aria-hidden="true" />
          <span className="hidden font-mono text-[10px] font-medium leading-none lg:block">
            New
          </span>
        </button>
      </div>

      {/* Spacer pushes settings to the bottom on desktop */}
      <div className="hidden flex-1 lg:block" />

      <div
        ref={accountRef}
        className="relative flex items-center justify-center lg:flex-col"
      >
        <AccountNavItem />
        {isAccountPanelOpen && (
          /* Anchored to the bottom on desktop: the avatar sits low in the
             strip, and a panel opened downward there runs off the screen
             with its own sign-out button hidden below the fold. */
          <div className="absolute bottom-full left-1/2 z-50 mb-1 lg:bottom-0 lg:left-full lg:top-auto lg:mb-0 lg:ml-1">
            <AccountPanel />
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={onOpenSettings}
        aria-label="Settings"
        aria-current={isSettingsOpen ? "page" : undefined}
        className={`${NAV_ITEM} ${isSettingsOpen ? NAV_ACTIVE : NAV_IDLE}`}
        title="Settings"
      >
        <LuSettings className="h-[18px] w-[18px]" aria-hidden="true" />
        <span className="hidden font-mono text-[10px] leading-none lg:block">
          Settings
        </span>
      </button>
    </nav>
  );
}

export default IconStrip;
