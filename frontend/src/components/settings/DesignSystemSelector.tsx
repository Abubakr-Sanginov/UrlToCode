import { LuChevronDown, LuPalette } from "react-icons/lu";
import { DesignSystem } from "../../types";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "../ui/select";

const NO_DESIGN_SYSTEM = "__none__";
const ADD_NEW = "__add_new__";
const MANAGE = "__manage__";

export interface DesignSystemSelectorProps {
  designSystems: DesignSystem[];
  selectedDesignSystemId: string | null;
  setSelectedDesignSystemId: (id: string | null) => void;
  onAddNew: () => void;
  onManage: () => void;
  disabled?: boolean;
  compact?: boolean;
}

function DesignSystemSelector({
  designSystems,
  selectedDesignSystemId,
  setSelectedDesignSystemId,
  onAddNew,
  onManage,
  disabled = false,
  compact = false,
}: DesignSystemSelectorProps) {
  const handleValueChange = (value: string) => {
    if (value === ADD_NEW) {
      onAddNew();
      return;
    }
    if (value === MANAGE) {
      onManage();
      return;
    }
    setSelectedDesignSystemId(value === NO_DESIGN_SYSTEM ? null : value);
  };

  const selectedDesignSystem = designSystems.find(
    (item) => item.id === selectedDesignSystemId
  );
  const hasSelection = selectedDesignSystem !== undefined;

  return (
    <Select
      value={selectedDesignSystemId ?? NO_DESIGN_SYSTEM}
      onValueChange={handleValueChange}
      disabled={disabled}
    >
      {compact ? (
        <SelectTrigger
          className={
            hasSelection
              ? "flex h-7 w-auto items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-0 text-xs font-medium text-foreground shadow-none hover:bg-accent focus:ring-0 focus:ring-offset-0 [&>svg:last-child]:hidden"
              : "flex h-7 w-7 items-center justify-center rounded-lg border-0 bg-transparent p-0 text-muted-foreground shadow-none hover:bg-accent hover:text-foreground focus:ring-0 focus:ring-offset-0 [&>svg:last-child]:hidden"
          }
          data-testid="design-system-select"
          aria-label={
            hasSelection
              ? `Design system: ${selectedDesignSystem.name}`
              : "Add a design system"
          }
          title={
            hasSelection
              ? `Design system: ${selectedDesignSystem.name}`
              : "Add a design system"
          }
        >
          <LuPalette className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {hasSelection && (
            <>
              <span className="max-w-[120px] truncate">
                {selectedDesignSystem.name}
              </span>
              <LuChevronDown
                className="h-3 w-3 shrink-0 opacity-60"
                aria-hidden="true"
              />
            </>
          )}
        </SelectTrigger>
      ) : hasSelection ? (
        <div className="grid grid-cols-3 items-center gap-4 text-sm">
          <span>Design system:</span>
          <SelectTrigger
            className="col-span-2"
            data-testid="design-system-select"
          >
            <SelectValue placeholder="No design system" />
          </SelectTrigger>
        </div>
      ) : (
        <div className="flex justify-end text-xs">
          <SelectTrigger
            className="h-auto w-auto justify-start gap-1 border-0 bg-transparent px-0 py-0 text-muted-foreground shadow-none hover:text-foreground focus:ring-0 focus:ring-offset-0 [&>svg]:hidden"
            data-testid="design-system-select"
          >
            <span>+ Add design system</span>
          </SelectTrigger>
        </div>
      )}
      <SelectContent>
        <SelectGroup>
          <SelectItem value={NO_DESIGN_SYSTEM}>No design system</SelectItem>
          {designSystems.map((designSystem) => (
            <SelectItem key={designSystem.id} value={designSystem.id}>
              {designSystem.name}
            </SelectItem>
          ))}
        </SelectGroup>
        <SelectSeparator />
        <SelectGroup>
          <SelectItem value={ADD_NEW} data-testid="design-system-add-new">
            + New design system…
          </SelectItem>
          {designSystems.length > 0 && (
            <SelectItem value={MANAGE} data-testid="design-system-manage">
              Manage design systems…
            </SelectItem>
          )}
        </SelectGroup>
      </SelectContent>
    </Select>
  );
}

export default DesignSystemSelector;
