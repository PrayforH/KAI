"use client";
import { useEffect, useState } from "react";
import { DEFAULT_VIRTUAL_FILE_METRICS, registerCustomTheme, preloadHighlighter, getFiletypeFromFileName, type ThemeRegistration } from "@pierre/diffs";
import codexDark from "../../lib/code-themes/codex-dark.json";
import codexLight from "../../lib/code-themes/codex-light.json";
const registration = globalThis as typeof globalThis & { __kaiCodexThemesRegistered?: boolean };
if (!registration.__kaiCodexThemesRegistered) {
  registerCustomTheme("kai-codex-dark", async () => ({ ...codexDark, name: "kai-codex-dark" }) as ThemeRegistration);
  registerCustomTheme("kai-codex-light", async () => ({ ...codexLight, name: "kai-codex-light" }) as ThemeRegistration);
  registration.__kaiCodexThemesRegistered = true;
}
export const codeMetrics = { ...DEFAULT_VIRTUAL_FILE_METRICS, lineHeight: 21.6 };
export const codeThemes = { dark: "kai-codex-dark", light: "kai-codex-light" };
export function useCodeHighlight(path: string) {
  const [loaded, setLoaded] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false; setFailed(false);
    void preloadHighlighter({ themes: Object.values(codeThemes), langs: [getFiletypeFromFileName(path)] })
      .then(() => { if (!cancelled) setLoaded(path); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [path]);
  return { ready: loaded === path, failed };
}
