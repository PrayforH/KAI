/**
 * 「在聊天中定位」的可见阶段。
 *
 * The chat rail indexes every turn, and after an old-message locate the thread
 * is re-imported and re-rendered. Measuring hundreds of turns in the same
 * frames as the landing scroll is what made the jump hitch, so the rail waits
 * for `idle` and then scans once.
 *
 * Phases are per thread: a locate in one pane must not freeze the rail of
 * another one. Entries return to `idle` and are dropped from the map.
 */

export type LocatePhase = "idle" | "walking" | "landing";

/** Fired on window when a locate has scrolled to its target. */
export const LOCATE_LANDED_EVENT = "harness:chat-located";

/** How long the landing scroll may settle before the rail measures again. */
export const LOCATE_LANDING_SETTLE_MS = 360;

/** How long the landed tick stays highlighted in the rail. */
export const LOCATE_LANDED_PULSE_MS = 1_800;

type Listener = (threadId: string, phase: LocatePhase) => void;

const phases = new Map<string, LocatePhase>();
const generations = new Map<string, number>();
const settleTimers = new Map<string, ReturnType<typeof setTimeout>>();
const listeners = new Set<Listener>();

function keyOf(threadId: string | undefined): string {
  return threadId ?? "";
}

function emit(threadId: string, phase: LocatePhase): void {
  for (const listener of [...listeners]) listener(threadId, phase);
}

export function getLocatePhase(threadId?: string): LocatePhase {
  return phases.get(keyOf(threadId)) ?? "idle";
}

export function subscribeLocatePhase(listener: Listener): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/**
 * Start a locate. Returns a generation token so a settle from a superseded
 * walk cannot end the phase of the walk that replaced it.
 */
export function beginLocate(threadId?: string): number {
  const thread = keyOf(threadId);
  const generation = (generations.get(thread) ?? 0) + 1;
  generations.set(thread, generation);
  const timer = settleTimers.get(thread);
  if (timer) {
    clearTimeout(timer);
    settleTimers.delete(thread);
  }
  phases.set(thread, "walking");
  emit(thread, "walking");
  return generation;
}

/**
 * End a locate. A landed locate keeps `landing` for the scroll-settle window;
 * the rail measures once after it flips back to `idle`. A miss or failure is
 * idle immediately — there is no landing to protect.
 */
export function settleLocate(threadId: string | undefined, generation: number, landed: boolean): void {
  const thread = keyOf(threadId);
  if ((generations.get(thread) ?? 0) !== generation) return;
  if (!landed) {
    phases.set(thread, "idle");
    generations.delete(thread);
    emit(thread, "idle");
    return;
  }
  phases.set(thread, "landing");
  emit(thread, "landing");
  const timer = setTimeout(() => {
    if ((generations.get(thread) ?? 0) !== generation) return;
    phases.set(thread, "idle");
    generations.delete(thread);
    settleTimers.delete(thread);
    emit(thread, "idle");
  }, LOCATE_LANDING_SETTLE_MS);
  settleTimers.set(thread, timer);
}

/** Test seam: drop every pending phase and timer. */
export function resetLocatePhases(): void {
  for (const timer of settleTimers.values()) clearTimeout(timer);
  settleTimers.clear();
  generations.clear();
  const threads = [...phases.keys()];
  phases.clear();
  for (const thread of threads) emit(thread, "idle");
}
