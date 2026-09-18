"use client";

import { useCallback, useEffect, useState } from "react";

export const COLOR_MODE_STORAGE_KEY = "agent-harness-color-mode";

export type ColorMode = "light" | "dark";

export function isColorMode(value: string | null): value is ColorMode {
  return value === "light" || value === "dark";
}

export function applyColorMode(mode: ColorMode) {
  const root = document.documentElement;
  root.dataset.colorMode = mode;
  root.style.colorScheme = mode;
  document
    .querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')
    .forEach((meta) => {
      meta.content = mode === "dark" ? "#181818" : "#ffffff";
    });
}

export function readStoredColorMode(): ColorMode {
  const stored = window.localStorage.getItem(COLOR_MODE_STORAGE_KEY);
  if (isColorMode(stored)) return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

export function selectColorMode(next: ColorMode) {
  try {
    window.localStorage.setItem(COLOR_MODE_STORAGE_KEY, next);
  } catch {
    // The selected theme still applies for the current page.
  }
  applyColorMode(next);
}

/**
 * Current color mode with cross-tab sync. Returns null before the client
 * effect runs so server and first client paint agree.
 */
export function useColorMode(): {
  mode: ColorMode | null;
  setColorMode: (next: ColorMode) => void;
} {
  const [mode, setMode] = useState<ColorMode | null>(null);

  useEffect(() => {
    const sync = () => {
      const next = readStoredColorMode();
      applyColorMode(next);
      setMode(next);
    };
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    sync();
    window.addEventListener("storage", sync);
    media.addEventListener("change", sync);
    return () => {
      window.removeEventListener("storage", sync);
      media.removeEventListener("change", sync);
    };
  }, []);

  const setColorMode = useCallback((next: ColorMode) => {
    selectColorMode(next);
    setMode(next);
  }, []);

  return { mode, setColorMode };
}
