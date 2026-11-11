// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { locateInChat, registerHistoryLocator } from "../src/lib/chat-locate";
import { clearLocatedMessages } from "../src/lib/locate-cache";
import {
  createThreadHistoryAdapter,
  invalidateThreadHistory,
  loadFullThreadHistory,
  useThreadHistoryPagination,
} from "../src/lib/task-history";

vi.mock("../src/lib/client-auth", () => ({
  // Synchronous on purpose: loadFullThreadHistory reads `.status` directly.
  requireAuthenticatedResponse: (response: Response) => response,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const THREADS = ["thread-500", "thread-700", "thread-cache", "thread-race", "thread-a", "thread-b"];

let host: HTMLDivElement;
let root: Root;
let frame: HTMLDivElement;
let viewport: HTMLDivElement;
let state: ReturnType<typeof useThreadHistoryPagination>;
let importCount = 0;
let importedIds: string[] = [];

type ImportRepository = Parameters<typeof useThreadHistoryPagination>[1]["importRepository"];

function importRepository(repository: Parameters<ImportRepository>[0]): void {
  importCount += 1;
  for (const item of repository.messages) {
    const id = item.message.id;
    importedIds.push(id);
    if (viewport.querySelector(`[data-message-id="${id}"]`)) continue;
    const node = document.createElement("div");
    node.setAttribute("data-message-id", id);
    node.scrollIntoView = vi.fn();
    viewport.append(node);
  }
}

function Harness({ threadId }: { threadId: string }) {
  state = useThreadHistoryPagination(threadId, { importRepository });
  return null;
}

async function render(threadId: string) {
  await act(async () => {
    root.render(<Harness threadId={threadId} />);
    await new Promise(resolve => setTimeout(resolve, 10));
  });
}

async function bootstrap(threadId: string) {
  const adapter = createThreadHistoryAdapter(threadId, {});
  await act(async () => { await adapter.loadSnapshot(); });
  adapter.dispose();
}

async function settle() {
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
}

function register(threadId: string) {
  registerHistoryLocator({
    threadId,
    ready: () => state.ready,
    hasMore: () => state.hasMore,
    loadEarlier: state.loadEarlier,
    loadUntil: state.loadUntil,
    waitForLoading: state.waitForLoading,
    frame: () => frame,
  });
}

/**
 * A faithful paged history endpoint: latest page is 10 runs, earlier pages
 * honour the requested limit and cursor. Records every call.
 */
function historyServer(total: number, options: { gate?: () => Promise<void> } = {}) {
  return vi.fn(async (input: unknown) => {
    const url = String(input);
    const params = new URL(url, "http://console.local").searchParams;
    const limit = Number(params.get("limit") ?? "10");
    const before = params.get("before");
    if (before && options.gate) await options.gate();
    const start = before ? Number(before.slice(2)) : total - 1;
    const oldest = Math.max(0, start - limit + 1);
    const messages = Array.from({ length: start - oldest + 1 }, (_, offset) => {
      const index = oldest + offset;
      return {
        id: `msg-${index}`,
        role: index % 2 === 0 ? "user" : "assistant",
        content: `run ${index}`,
      };
    });
    const hasMore = oldest > 0;
    return new Response(JSON.stringify({
      thread_id: "thread",
      status: "succeeded",
      messages,
      ...(hasMore
        ? { next_cursor: `c-${oldest - 1}`, has_more: true }
        : { has_more: false }),
      total,
    }), { status: 200, headers: { "content-type": "application/json" } });
  });
}

function locateRequests(fetcher: ReturnType<typeof historyServer>) {
  return fetcher.mock.calls
    .map(call => String(call[0]))
    .filter(url => url.includes("limit=100") && url.includes("before="));
}

beforeEach(() => {
  for (const threadId of THREADS) invalidateThreadHistory(threadId);
  clearLocatedMessages();
  importCount = 0;
  importedIds = [];
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  frame = document.createElement("div");
  viewport = document.createElement("div");
  viewport.className = "aui-thread-viewport";
  frame.append(viewport);
  document.body.append(frame);
});

afterEach(() => {
  act(() => root.unmount());
  registerHistoryLocator(null);
  host.remove();
  frame.remove();
  vi.unstubAllGlobals();
});

async function locate(threadId: string, messageId: string) {
  let outcome: Awaited<ReturnType<typeof locateInChat>> | undefined;
  await act(async () => {
    outcome = await locateInChat(messageId, threadId);
  });
  return outcome;
}

describe("id-driven locate walk", () => {
  it("locates 500 runs back with 5 pages and exactly one import", async () => {
    const fetcher = historyServer(500);
    vi.stubGlobal("fetch", fetcher);
    await render("thread-500");
    await bootstrap("thread-500");
    await settle();
    register("thread-500");

    const started = performance.now();
    const outcome = await locate("thread-500", "msg-30");
    const elapsed = performance.now() - started;

    expect(outcome).toBe("located");
    const pages = locateRequests(fetcher);
    expect(pages).toHaveLength(5);
    expect(pages[0]).toContain("limit=100");
    expect(pages[0]).toContain("before=c-489");
    // One import for the whole walk — not one per page.
    expect(importCount).toBe(1);
    expect(importedIds).toContain("msg-30");
    // No blind 1.2 s window per page: the whole walk stays in frame territory.
    expect(elapsed).toBeLessThan(500);
    console.info(`[locate bench] 500 runs: pages=${pages.length} imports=${importCount} elapsed=${elapsed.toFixed(1)}ms`);
  }, 20_000);

  it("still locates past the old 630-run ceiling", async () => {
    const fetcher = historyServer(700);
    vi.stubGlobal("fetch", fetcher);
    await render("thread-700");
    await bootstrap("thread-700");
    await settle();
    register("thread-700");

    const started = performance.now();
    const outcome = await locate("thread-700", "msg-20");
    const elapsed = performance.now() - started;

    expect(outcome).toBe("located");
    expect(locateRequests(fetcher)).toHaveLength(7);
    expect(importCount).toBe(1);
    expect(elapsed).toBeLessThan(500);
    console.info(`[locate bench] 700 runs: pages=7 imports=${importCount} elapsed=${elapsed.toFixed(1)}ms`);
  }, 20_000);

  it("reports missing only after the thread's real end, without importing", async () => {
    const fetcher = historyServer(500);
    vi.stubGlobal("fetch", fetcher);
    await render("thread-500");
    await bootstrap("thread-500");
    await settle();
    register("thread-500");

    const outcome = await locate("thread-500", "msg-absent");

    expect(outcome).toBe("missing");
    expect(locateRequests(fetcher)).toHaveLength(5);
    expect(importCount).toBe(0);
  }, 20_000);

  it("reuses the history drawer's fetched pages instead of walking again", async () => {
    const fetcher = historyServer(500);
    vi.stubGlobal("fetch", fetcher);
    // Opening the drawer fetches the whole thread (100-run pages).
    await act(async () => { await loadFullThreadHistory("thread-cache"); });
    const callsAfterDrawer = fetcher.mock.calls.length;

    await render("thread-cache");
    await bootstrap("thread-cache");
    await settle();
    register("thread-cache");
    const callsAfterBootstrap = fetcher.mock.calls.length;
    expect(callsAfterBootstrap - callsAfterDrawer).toBe(1); // just the latest page

    const outcome = await locate("thread-cache", "msg-30");

    expect(outcome).toBe("located");
    expect(importCount).toBe(1);
    // The cached pages already contain msg-30: the locate adds no request at all.
    expect(fetcher.mock.calls.length).toBe(callsAfterBootstrap);
  }, 20_000);

  it("shares one walk between two clicks on the same message", async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    const fetcher = historyServer(500, { gate: () => gate });
    vi.stubGlobal("fetch", fetcher);
    await render("thread-race");
    await bootstrap("thread-race");
    await settle();
    register("thread-race");

    let first!: Promise<Awaited<ReturnType<typeof locateInChat>>>;
    let second!: Promise<Awaited<ReturnType<typeof locateInChat>>>;
    await act(async () => {
      first = locateInChat("msg-450", "thread-race");
      second = locateInChat("msg-450", "thread-race");
      release();
      await Promise.all([first, second]);
    });

    expect(first).toBe(second);
    await expect(first).resolves.toBe("located");
    expect(locateRequests(fetcher)).toHaveLength(1);
    expect(importCount).toBe(1);
  }, 20_000);

  it("reports retry, not missing, when the thread is switched mid-walk", async () => {
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    vi.stubGlobal("fetch", historyServer(500, { gate: () => gate }));
    await render("thread-a");
    await bootstrap("thread-a");
    await settle();
    register("thread-a");

    let pending!: Promise<Awaited<ReturnType<typeof locateInChat>>>;
    await act(async () => {
      pending = locateInChat("msg-30", "thread-a");
      // A different thread takes the frame while the walk is in flight.
      await render("thread-b");
    });
    release();
    await expect(pending).resolves.toBe("retry");
    expect(importCount).toBe(0);
  }, 20_000);
});
