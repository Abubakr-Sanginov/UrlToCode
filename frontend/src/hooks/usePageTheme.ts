import { useEffect } from "react";
import { AppTheme } from "../types";

/**
 * Applies the saved theme on pages that live outside `App`.
 *
 * `App` owns the theme and applies it to the document, but the welcome and
 * profile pages are separate routes that never mount it. Without this they
 * would render with whatever class the document happened to have, which is
 * the light palette on a first visit. The product is dark-first, so that is
 * also the fallback when nothing is saved.
 */
export function usePageTheme(): void {
  useEffect(() => {
    let theme: AppTheme = AppTheme.DARK;
    try {
      const saved = window.localStorage.getItem("app-theme");
      if (saved) theme = JSON.parse(saved) as AppTheme;
    } catch {
      // An unreadable value is the same as none: stay on the default.
    }

    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark =
      theme === AppTheme.DARK || (theme === AppTheme.SYSTEM && prefersDark);
    document.documentElement.classList.toggle("dark", isDark);
    document.body.classList.toggle("dark", isDark);
  }, []);
}
