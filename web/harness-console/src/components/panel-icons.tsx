"use client";

/**
 * Panel glyphs shared by the header utilities and the drawer it opens, so the
 * same mark opens and closes the same panel.
 */

export function SidebarPanelIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.75" y="4.25" width="14.5" height="11.5" rx="2.5" />
      <path d="M13.5 4.25v11.5" />
    </svg>
  );
}

export function SidebarLeftIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.75" y="4.25" width="14.5" height="11.5" rx="2.5" />
      <path d="M6.75 4.25v11.5" />
    </svg>
  );
}
