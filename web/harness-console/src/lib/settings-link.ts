import type { MouseEvent } from "react";

type Router = { push: (href: string) => void };

function isModified(event: MouseEvent<HTMLAnchorElement>): boolean {
  return (
    event.button !== 0 ||
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey
  );
}

/** Settings keeps the current in-app location so leaving settings returns here. */
export function settingsHref(hash = ""): string {
  const from = `${window.location.pathname}${window.location.search}`;
  if (from === "/settings" || from.startsWith("/settings?") || from.startsWith("/settings#")) {
    return `/settings${hash}`;
  }
  return `/settings?returnTo=${encodeURIComponent(from)}${hash}`;
}

export function openSettings(
  event: MouseEvent<HTMLAnchorElement>,
  router: Router,
  hash = "",
): void {
  if (event.defaultPrevented || isModified(event)) return;
  event.preventDefault();
  router.push(settingsHref(hash));
}

/** Only same-origin absolute paths are accepted, so returnTo cannot leave the app. */
export function safeReturnTo(value: string | null): string | null {
  if (!value || !value.startsWith("/")) return null;
  if (value.startsWith("//") || value.startsWith("/\\")) return null;
  return value;
}
