import React from "react";
import { BsArrowReturnLeft } from "react-icons/bs";

interface KeyboardShortcutBadgeProps {
  letter: string;
}

const KeyboardShortcutBadge: React.FC<KeyboardShortcutBadgeProps> = ({
  letter,
}) => {
  const icon =
    letter.toLowerCase() === "enter" || letter.toLowerCase() === "return" ? (
      <BsArrowReturnLeft />
    ) : (
      letter.toUpperCase()
    );

  return (
    <span className="ml-2 rounded border border-border bg-muted px-1.5 py-[2px] font-mono text-xs text-muted-foreground">
      {icon}
    </span>
  );
};

export default KeyboardShortcutBadge;
