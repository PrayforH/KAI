"use client";

import { useColorMode } from "../lib/color-mode";

export function ThemeSelector() {
  const { mode, setColorMode } = useColorMode();

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
          onClick={() => setColorMode(value)}
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
