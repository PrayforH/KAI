export type NavigationIntent = {
  currentHref: string;
  targetHref: string;
  button: number;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  target?: string | null;
  download?: boolean;
};

const HISTORY_GUARD_FIELD = "__agentStudioUnsavedNavigation";

type HistoryGuardMarker = {
  id: string;
  role: "base" | "guard";
};

export type HistoryAdapter = Pick<
  History,
  "back" | "forward" | "pushState" | "replaceState" | "state"
>;

export type UnsavedHistoryGuard = {
  activate: (href: string) => void;
  deactivate: (afterRemoval?: () => void) => void;
  handlePopState: (state: unknown) => "prompt" | "handled" | "ignored";
  isActive: () => boolean;
};

function historyStateWithMarker(
  state: unknown,
  marker: HistoryGuardMarker,
): Record<string, unknown> {
  const base = state && typeof state === "object"
    ? state as Record<string, unknown>
    : {};
  return { ...base, [HISTORY_GUARD_FIELD]: marker };
}

function historyGuardMarker(state: unknown): HistoryGuardMarker | null {
  if (!state || typeof state !== "object") return null;
  const marker = (state as Record<string, unknown>)[HISTORY_GUARD_FIELD];
  if (!marker || typeof marker !== "object") return null;
  const id = (marker as Record<string, unknown>).id;
  const role = (marker as Record<string, unknown>).role;
  return typeof id === "string" && (role === "base" || role === "guard")
    ? { id, role }
    : null;
}

export function createUnsavedHistoryGuard(
  history: HistoryAdapter,
  guardId: string,
): UnsavedHistoryGuard {
  let phase: "inactive" | "guarded" | "restoring" | "removing" = "inactive";
  let originalState: unknown;
  let originalHref = "";
  let removeRequested = false;
  let afterRemoval: Array<() => void> = [];

  const matches = (state: unknown, role: HistoryGuardMarker["role"]) => {
    const marker = historyGuardMarker(state);
    return marker?.id === guardId && marker.role === role;
  };

  const flushRemoval = () => {
    const callbacks = afterRemoval;
    afterRemoval = [];
    for (const callback of callbacks) callback();
  };

  const beginRemoval = () => {
    if (phase === "inactive" || phase === "removing") return;
    removeRequested = false;
    phase = "removing";
    history.back();
  };

  return {
    activate(href) {
      if (phase !== "inactive") return;
      originalState = history.state;
      originalHref = href;
      history.replaceState(
        historyStateWithMarker(originalState, { id: guardId, role: "base" }),
        "",
        href,
      );
      history.pushState(
        historyStateWithMarker(originalState, { id: guardId, role: "guard" }),
        "",
        href,
      );
      phase = "guarded";
    },
    deactivate(callback) {
      if (callback) afterRemoval.push(callback);
      if (phase === "inactive") {
        flushRemoval();
      } else if (phase === "restoring") {
        removeRequested = true;
      } else {
        beginRemoval();
      }
    },
    handlePopState(state) {
      if (phase === "restoring" && matches(state, "guard")) {
        phase = "guarded";
        if (removeRequested) beginRemoval();
        return "handled";
      }
      if (phase === "removing" && matches(state, "base")) {
        history.replaceState(originalState, "", originalHref);
        phase = "inactive";
        removeRequested = false;
        flushRemoval();
        return "handled";
      }
      if (phase === "guarded" && matches(state, "base")) {
        phase = "restoring";
        history.forward();
        return "prompt";
      }
      return "ignored";
    },
    isActive() {
      return phase !== "inactive";
    },
  };
}

export function guardedNavigationDestination(
  intent: NavigationIntent,
): URL | null {
  if (
    intent.button !== 0
    || intent.altKey
    || intent.ctrlKey
    || intent.metaKey
    || intent.shiftKey
    || intent.download
    || (intent.target && intent.target !== "_self")
  ) {
    return null;
  }

  let current: URL;
  let destination: URL;
  try {
    current = new URL(intent.currentHref);
    destination = new URL(intent.targetHref, current);
  } catch {
    return null;
  }
  if (!['http:', 'https:'].includes(destination.protocol)) return null;
  if (destination.href === current.href) return null;
  if (
    destination.origin === current.origin
    && destination.pathname === current.pathname
    && destination.search === current.search
  ) {
    return null;
  }
  return destination;
}

export function navigationLabel(text: string | null, destination: URL): string {
  const compact = text?.replace(/\s+/g, " ").trim();
  return compact ? compact.slice(0, 48) : destination.pathname;
}
