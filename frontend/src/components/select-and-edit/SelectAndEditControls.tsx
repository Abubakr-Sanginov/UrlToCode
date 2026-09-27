import { LuMousePointerClick, LuX } from "react-icons/lu";
import { useAppStore } from "../../store/app-store";

// Select-and-edit toggle in the preview toolbar, next to the device/code
// tabs — the "inspect element" spot users know from devtools. While select
// mode is on it becomes an explicit exit button.
export function SelectAndEditToolbarButton() {
  const { inSelectAndEditMode, toggleInSelectAndEditMode } = useAppStore();
  return (
    <button
      type="button"
      onClick={toggleInSelectAndEditMode}
      data-testid="select-edit-toggle"
      title={
        inSelectAndEditMode
          ? "Exit selection mode"
          : "Select an element in the preview to target your edit"
      }
      className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
        inSelectAndEditMode
          ? "border-brand bg-brand text-brand-foreground hover:bg-brand-muted"
          : "border-border bg-card text-muted-foreground hover:border-input hover:text-foreground"
      }`}
    >
      {inSelectAndEditMode ? (
        <>
          <LuX className="h-3.5 w-3.5" aria-hidden="true" />
          Exit select mode
        </>
      ) : (
        <>
          <LuMousePointerClick className="h-3.5 w-3.5" aria-hidden="true" />
          Select &amp; edit
        </>
      )}
    </button>
  );
}
