import type { Prefs } from "../types";

const KEY = "resonance.theme";
type Theme = NonNullable<Prefs["theme"]>;
const COLORS = { light: "#FFFFFF", dark: "#000000" };

export function storedTheme(): Theme {
  try { const v = localStorage.getItem(KEY); if (v === "light" || v === "dark") return v; } catch { /* private mode */ }
  return "system";
}

/** Applies the choice at once (no flash on start) and keeps the browser's toolbar colour in step. */
export function applyTheme(theme: Theme) {
  try { localStorage.setItem(KEY, theme); } catch { /* private mode */ }
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme"); else root.dataset.theme = theme;
  document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]').forEach((m) => {
    m.dataset.media ??= m.media; // remember the page's own media query and colour so "system" can restore them
    m.dataset.color ??= m.content;
    if (theme === "system") { m.media = m.dataset.media; m.content = m.dataset.color; }
    else { m.removeAttribute("media"); m.content = COLORS[theme]; }
  });
}
