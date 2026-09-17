import { requireAuthenticatedResponse } from "./client-auth";
import { readClientResource } from "./client-read-cache";

export type SteeringState = {
  available: boolean;
  requests: Array<{ request_id: string; status: string; error?: string }>;
};

class SteeringReadError extends Error {
  constructor(readonly status: number) { super(`Steering status HTTP ${status}`); }
}

function readSteeringState(runId: string): Promise<SteeringState> {
  const url = `/api/harness/runs/${encodeURIComponent(runId)}/steer`;
  return readClientResource(url, async () => {
    const response = requireAuthenticatedResponse(await fetch(url, { cache: "no-store" }));
    if (!response.ok) throw new SteeringReadError(response.status);
    return response.json() as Promise<SteeringState>;
  }, 0);
}

/** Complete each read before scheduling another; share reads across React remounts. */
export function startSteeringPolling(options: {
  runId: string;
  shouldPoll: () => boolean;
  onState: (state: SteeringState) => void;
  onError: () => void;
}): () => void {
  let active = true;
  let inFlight = false;
  let failures = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const visible = () => typeof document === "undefined" || document.visibilityState !== "hidden";
  async function poll() {
    if (!active || inFlight || !visible() || !options.shouldPoll()) return;
    clearTimeout(timer);
    inFlight = true;
    let delay = 2_000;
    try {
      const state = await readSteeringState(options.runId);
      if (!active) return;
      failures = 0;
      options.onState(state);
    } catch (error) {
      if (!active) return;
      failures += 1;
      if ((error instanceof SteeringReadError && [401, 403, 404, 405].includes(error.status)) || failures >= 4) {
        active = false;
        options.onError();
        return;
      }
      delay = Math.min(15_000, 2_000 * 2 ** failures);
    } finally {
      inFlight = false;
      if (active && visible() && options.shouldPoll()) timer = setTimeout(() => void poll(), delay);
    }
  }
  function visibilityChanged() {
    clearTimeout(timer);
    if (visible()) void poll();
  }
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", visibilityChanged);
  void poll();
  return () => {
    active = false;
    clearTimeout(timer);
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", visibilityChanged);
    // Let a shared read finish. Unmounted consumers ignore it; no cancelled request on state changes.
  };
}
