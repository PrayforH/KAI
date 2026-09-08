import type { RunViewModel } from "./run-view-model";

export interface ElapsedAnchor {
  runId: string;
  observedAt: number;
  elapsedMs: number;
}

const storageKey = "agent-studio:run-elapsed:v1";
const anchors = new Map<string, ElapsedAnchor>();
const activePhases = new Set(["queued", "running", "waiting_approval"]);

export function activeElapsedMs(
  view: RunViewModel,
  now: number | null,
  anchor: ElapsedAnchor,
) {
  if (!activePhases.has(view.phase) || now === null || anchor.runId !== view.runId) {
    return view.elapsedMs;
  }
  // Only compare timestamps from the same clock. Server/browser skew must
  // never become execution time (e.g. a fresh run starting at 40 seconds).
  return Math.max(view.elapsedMs, anchor.elapsedMs + Math.max(0, now - anchor.observedAt));
}

export function elapsedAnchorFor(view: RunViewModel, now: number): ElapsedAnchor {
  if (!anchors.has(view.runId) && typeof window !== "undefined") {
    try {
      const saved: unknown = JSON.parse(window.sessionStorage.getItem(storageKey) ?? "[]");
      if (Array.isArray(saved)) {
        for (const entry of saved.slice(-128)) {
          if (entry && typeof entry.runId === "string"
            && Number.isFinite(entry.observedAt) && Number.isFinite(entry.elapsedMs)
            && entry.elapsedMs >= 0 && !anchors.has(entry.runId)) {
            anchors.set(entry.runId, entry);
          }
        }
      }
    } catch {
      // The in-memory clock still survives task switching if storage is disabled.
    }
  }
  const existing = anchors.get(view.runId);
  if (existing && activeElapsedMs(view, now, existing) > view.elapsedMs) return existing;
  if (existing && existing.elapsedMs === view.elapsedMs) return existing;
  const anchor = { runId: view.runId, observedAt: now, elapsedMs: view.elapsedMs };
  anchors.delete(view.runId);
  anchors.set(view.runId, anchor);
  while (anchors.size > 128) anchors.delete(anchors.keys().next().value!);
  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify([...anchors.values()]));
  } catch {
    // Session storage is best effort; never block rendering on it.
  }
  return anchor;
}
