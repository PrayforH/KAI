// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invalidateClientReads } from "../src/lib/client-read-cache";
import { startSteeringPolling } from "../src/lib/steering-poller";

const stops: Array<() => void> = [];
function start(overrides: Partial<Parameters<typeof startSteeringPolling>[0]> = {}) {
  const options = { runId: "run-test", shouldPoll: () => true, onState: vi.fn(), onError: vi.fn(), ...overrides };
  stops.push(startSteeringPolling(options));
  return options;
}
const ok = (available = false) => Response.json({ available, requests: [] });
const flush = () => vi.advanceTimersByTimeAsync(0);
beforeEach(() => {
  vi.useFakeTimers();
  invalidateClientReads();
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
});
afterEach(() => {
  stops.splice(0).forEach((stop) => stop());
  vi.useRealTimers(); vi.unstubAllGlobals();
});

describe("steering status reads", () => {
  it("shares an in-flight read across remounts and ignores stale callbacks", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { finish = resolve; }));
    vi.stubGlobal("fetch", fetcher);
    const first = start(); stops[0](); const second = start();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    finish(ok()); await flush();
    expect(first.onState).not.toHaveBeenCalled();
    expect(second.onState).toHaveBeenCalledOnce();
  });
  it("waits for completion and stops polling once steering is ready", async () => {
    let available = false;
    const fetcher = vi.fn().mockResolvedValue(ok(true));
    vi.stubGlobal("fetch", fetcher);
    start({ shouldPoll: () => !available, onState: (state) => { available = state.available; } });
    await vi.advanceTimersByTimeAsync(30_000);
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("pauses while hidden and checks once on return", async () => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(ok())); vi.stubGlobal("fetch", fetcher);
    start(); await vi.advanceTimersByTimeAsync(20_000); expect(fetcher).not.toHaveBeenCalled();
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    document.dispatchEvent(new Event("visibilitychange")); await flush();
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it("stops after a missing run instead of repeatedly issuing failing requests", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response(null, { status: 404 })));
    vi.stubGlobal("fetch", fetcher); const options = start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledOnce(); expect(options.onError).toHaveBeenCalledOnce();
  });
  it("bounds retries and backs off after server failures", async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response(null, { status: 502 })));
    vi.stubGlobal("fetch", fetcher); const options = start();
    await vi.advanceTimersByTimeAsync(3_000); expect(fetcher).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetcher).toHaveBeenCalledTimes(4); expect(options.onError).toHaveBeenCalledOnce();
  });
});
