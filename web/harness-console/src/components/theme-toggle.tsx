"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "agent-harness-color-mode";

type ColorMode = "light" | "dark";

function isColorMode(value: string | null): value is ColorMode {
  return value === "light" || value === "dark";
}

function applyColorMode(mode: ColorMode) {
  const root = document.documentElement;
  root.dataset.colorMode = mode;
  root.style.colorScheme = mode;
  document
    .querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')
    .forEach((meta) => {
      meta.content = mode === "dark" ? "#181818" : "#ffffff";
    });
}

export function ThemeSelector() {
  const [mode, setMode] = useState<ColorMode | null>(null);

  const syncColorMode = useCallback(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    const next = isColorMode(stored)
      ? stored
      : window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    applyColorMode(next);
    setMode(next);
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    syncColorMode();
    window.addEventListener("storage", syncColorMode);
    media.addEventListener("change", syncColorMode);
    return () => {
      window.removeEventListener("storage", syncColorMode);
      media.removeEventListener("change", syncColorMode);
    };
  }, [syncColorMode]);

  function selectColorMode(next: ColorMode) {
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The selected theme still applies for the current page.
    }
    applyColorMode(next);
    setMode(next);
  }

  return (
    <div
      className="theme-selector"
      role="radiogroup"
      aria-label="界面主题"
      data-mode={mode ?? undefined}
    >
      {([
        ["light", "浅色", "清爽白底与克制蓝色强调，适合日常协作"],
        ["dark", "深色", "Codex 深色画布，适合长时间专注"],
      ] as const).map(([value, label, description]) => (
        <button
          className="theme-option"
          type="button"
          role="radio"
          aria-checked={mode === value}
          data-theme-option={value}
          onClick={() => selectColorMode(value)}
          key={value}
        >
          <span className="theme-option-preview" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span className="theme-option-copy">
            <strong>{label}</strong>
            <small>{description}</small>
          </span>
          <span className="theme-option-check" aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
