// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import {
  createThreadHistoryAdapter,
  invalidateThreadHistory,
  useAutoLoadEarlierMessages,
  useThreadHistoryPagination,
} from "../src/lib/task-history";

vi.mock("../src/lib/client-auth", () => ({
  requireAuthenticatedResponse: async (response: Response) => response,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const page = (
  messages: Array<{ id: string; role: string; content: string }>,
  extras: { next_cursor?: string | null; has_more?: boolean } = {},
) =>
  Response.json({
    thread_id: "thread-1",
    status: "succeeded",
    run_id: "run-latest",
    messages,
    ...(extras.next_cursor !== undefined ? { next_cursor: extras.next_cursor } : {}),
    ...(extras.has_more !== undefined ? { has_more: extras.has_more } : {}),
  });

const message = (id: string, role: string, content: string) => ({ id, role, content });

let host: HTMLDivElement;
let root: Root;
let state: ReturnType<typeof useThreadHistoryPagination>;
let importRepositoryMock: Mock;
type ImportRepository = Parameters<typeof useThreadHistoryPagination>[1]["importRepository"];
const importRepository: ImportRepository = (repository) => importRepositoryMock(repository);

function Harness({ threadId }: { threadId: string }) {
  state = useThreadHistoryPagination(threadId, { importRepository });
  return null;
}

async function render(threadId = "thread-1") {
  await act(async () => {
    root.render(<Harness threadId={threadId} />);
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

async function bootstrapLatestPage() {
  const adapter = createThreadHistoryAdapter("thread-1", {});
  await act(async () => {
    await adapter.loadSnapshot();
  });
  adapter.dispose();
}

async function settle() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 10));
  });
}

beforeEach(() => {
  invalidateThreadHistory("thread-1");
  importRepositoryMock = vi.fn();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("thread history pagination", () => {
  it("seeds the accumulated window from the latest page fetch", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      page([message("user-2", "user", "second")], { next_cursor: "cursor-1", has_more: true }),
    );
    vi.stubGlobal("fetch", fetcher);

    await render();
    await bootstrapLatestPage();
    await settle();

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toContain("history?limit=10");
    expect(state.hasMore).toBe(true);
    expect(state.loading).toBe(false);
  });

  it("prepends the earlier page and imports the merged repository", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        page(
          [message("user-2", "user", "second"), message("assistant-2", "assistant", "b2")],
          { next_cursor: "cursor-1", has_more: true },
        ),
      )
      .mockResolvedValueOnce(
        page([message("user-1", "user", "first")], { next_cursor: null, has_more: false }),
      );
    vi.stubGlobal("fetch", fetcher);

    await render();
    await bootstrapLatestPage();
    await settle();
    expect(state.hasMore).toBe(true);

    await act(async () => {
      await state.loadEarlier();
    });

    expect(fetcher).toHaveBeenCalledTimes(2);
    const earlierUrl = fetcher.mock.calls[1][0] as string;
    expect(earlierUrl).toContain("before=cursor-1");
    // Earlier pages carry more runs per gesture; the latest page stays small.
    expect(earlierUrl).toContain("limit=30");
    expect(importRepositoryMock).toHaveBeenCalledTimes(1);
    const repository = importRepositoryMock.mock.calls[0][0] as {
      messages: Array<{ message: { content: unknown } }>;
    };
    const textOf = (content: unknown): string =>
      typeof content === "string"
        ? content
        : Array.isArray(content)
          ? content.map((part: { text?: string }) => part.text ?? "").join("")
          : "";
    const contents = repository.messages.map((item) => textOf(item.message.content));
    expect(contents).toEqual(["first", "second", "b2"]);
    expect(state.hasMore).toBe(false);
  });

  it("loads several earlier pages in one import when a locate jump asks for them", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        page([message("user-4", "user", "fourth")], { next_cursor: "cursor-1", has_more: true }),
      )
      .mockResolvedValueOnce(
        page([message("user-3", "user", "third")], { next_cursor: "cursor-2", has_more: true }),
      )
      .mockResolvedValueOnce(
        page([message("user-2", "user", "second")], { next_cursor: "cursor-3", has_more: true }),
      )
      .mockResolvedValueOnce(
        page([message("user-1", "user", "first")], { next_cursor: null, has_more: false }),
      );
    vi.stubGlobal("fetch", fetcher);

    await render();
    await bootstrapLatestPage();
    await settle();
    await act(async () => {
      await state.loadEarlier(3);
    });

    // One gesture, one import — not one repository re-import per page.
    expect(fetcher).toHaveBeenCalledTimes(4);
    expect(importRepositoryMock).toHaveBeenCalledTimes(1);
    const repository = importRepositoryMock.mock.calls[0][0] as {
      messages: Array<{ message: { content: unknown } }>;
    };
    const textOf = (content: unknown): string =>
      typeof content === "string"
        ? content
        : Array.isArray(content)
          ? content.map((part: { text?: string }) => part.text ?? "").join("")
          : "";
    expect(repository.messages.map(item => textOf(item.message.content)))
      .toEqual(["first", "second", "third", "fourth"]);
    expect(state.hasMore).toBe(false);
  });

  it("keeps the earlier page fetch out of the page-1 request dedupe", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        page([message("user-2", "user", "second")], { next_cursor: "cursor-1", has_more: true }),
      )
      .mockResolvedValueOnce(
        page([message("user-1", "user", "first")], { next_cursor: null, has_more: false }),
      );
    vi.stubGlobal("fetch", fetcher);

    await render();
    await bootstrapLatestPage();
    await settle();
    await act(async () => {
      await state.loadEarlier();
    });

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect((fetcher.mock.calls[0][0] as string).includes("before=")).toBe(false);
    expect((fetcher.mock.calls[1][0] as string).includes("before=cursor-1")).toBe(true);
  });

  it("does not import anything while no earlier page is pending", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(page([message("user-1", "user", "only")], { has_more: false }));
    vi.stubGlobal("fetch", fetcher);

    await render();
    await bootstrapLatestPage();
    await settle();

    expect(state.hasMore).toBe(false);
    await act(async () => {
      await state.loadEarlier();
    });
    expect(importRepositoryMock).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("retains an expanded window and cursor when the latest page refreshes", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(page([message("user-3", "user", "third")], { next_cursor: "second", has_more: true }))
      .mockResolvedValueOnce(page([message("user-2", "user", "second")], { next_cursor: "first", has_more: true }))
      .mockResolvedValueOnce(page([message("user-3", "user", "third"), message("user-4", "user", "fourth")], { next_cursor: "second", has_more: true }))
      // A moving cursor can overlap one previously loaded message.
      .mockResolvedValueOnce(page([message("user-1", "user", "first"), message("user-2", "user", "second")], { has_more: false }));
    vi.stubGlobal("fetch", fetcher);
    await render();
    await bootstrapLatestPage();
    await act(async () => { await state.loadEarlier(); });
    await bootstrapLatestPage();
    await act(async () => { await state.loadEarlier(); });
    expect(fetcher.mock.calls[3][0]).toContain("before=first");
    expect(importRepositoryMock.mock.lastCall?.[0].messages.map((entry: { message: { id: string } }) => entry.message.id))
      .toEqual(["user-1", "user-2", "user-3", "user-4"]);
  });

  it("never imports an in-flight earlier page into a newly selected assistant", async () => {
    let resolvePage!: (response: Response) => void;
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(page([message("user-2", "user", "second")], { next_cursor: "first", has_more: true }))
      .mockImplementationOnce(() => new Promise<Response>(resolve => { resolvePage = resolve; })));
    await render();
    await bootstrapLatestPage();
    let pending!: Promise<boolean>;
    await act(async () => { pending = state.loadEarlier(); });
    await render("other-assistant");
    await act(async () => { resolvePage(page([message("user-1", "user", "first")])); await pending; });
    expect(importRepositoryMock).not.toHaveBeenCalled();
  });
});

describe("auto load earlier on scroll", () => {
  function Harness({ frameRef, pagination, readerDriven = false }: {
    frameRef: { current: HTMLElement | null };
    pagination: Parameters<typeof useAutoLoadEarlierMessages>[1];
    readerDriven?: boolean;
  }) {
    useAutoLoadEarlierMessages(frameRef, pagination, { readerDriven });
    return null;
  }

  function viewportWithMetrics(scrollHeight: number, clientHeight: number) {
    const frame = document.createElement("div");
    const viewport = document.createElement("div");
    viewport.className = "aui-thread-viewport";
    let height = scrollHeight;
    Object.defineProperty(viewport, "scrollHeight", { get: () => height, configurable: true });
    Object.defineProperty(viewport, "clientHeight", { get: () => clientHeight, configurable: true });
    viewport.scrollTop = 0;
    frame.append(viewport);
    document.body.append(frame);
    return { frame, viewport, setHeight: (next: number) => { height = next; } };
  }

  it("loads the previous page when the reader reaches the top", async () => {
    const { frame, viewport, setHeight } = viewportWithMetrics(2000, 600);
    const loadEarlier = vi.fn(async () => { setHeight(3000); return true; });
    const pagination = { hasMore: true, loading: false, loadEarlier };
    const frameRef = { current: frame };
    await act(async () => {
      root.render(<Harness frameRef={frameRef} pagination={pagination} />);
    });

    viewport.scrollTop = 900;
    await act(async () => { viewport.dispatchEvent(new Event("scroll")); });
    expect(loadEarlier).not.toHaveBeenCalled();

    viewport.scrollTop = 40;
    await act(async () => {
      viewport.dispatchEvent(new Event("scroll"));
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
    expect(loadEarlier).toHaveBeenCalledTimes(1);
    frame.remove();
  });

  it("keeps the reader's place by the height that was prepended", async () => {
    const { frame, viewport, setHeight } = viewportWithMetrics(2000, 600);
    const loadEarlier = vi.fn(async () => { setHeight(2600); return true; });
    const pagination = { hasMore: true, loading: false, loadEarlier };
    await act(async () => {
      root.render(<Harness frameRef={{ current: frame }} pagination={pagination} />);
    });
    viewport.scrollTop = 0;
    await act(async () => {
      viewport.dispatchEvent(new Event("scroll"));
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
    expect(viewport.scrollTop).toBe(600);
    frame.remove();
  });

  it("stops when the thread has no earlier runs", async () => {
    const { frame, viewport } = viewportWithMetrics(2000, 600);
    const loadEarlier = vi.fn(async () => false);
    await act(async () => {
      root.render(<Harness frameRef={{ current: frame }}
        pagination={{ hasMore: false, loading: false, loadEarlier }} />);
    });
    viewport.scrollTop = 0;
    await act(async () => {
      viewport.dispatchEvent(new Event("scroll"));
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
    expect(loadEarlier).not.toHaveBeenCalled();
    frame.remove();
  });

  it("fills a viewport that is shorter than the first page", async () => {
    const { frame, setHeight } = viewportWithMetrics(300, 600);
    const loadEarlier = vi.fn(async () => { setHeight(1200); return true; });
    await act(async () => {
      root.render(<Harness frameRef={{ current: frame }}
        pagination={{ hasMore: true, loading: false, loadEarlier }} />);
      await new Promise((resolve) => setTimeout(resolve, 40));
    });
    expect(loadEarlier).toHaveBeenCalledTimes(1);
    frame.remove();
  });

  it("opens an assistant without auto-filling or reacting to programmatic scrolls", async () => {
    const { frame, viewport } = viewportWithMetrics(300, 600);
    const loadEarlier = vi.fn(async () => false);
    await act(async () => root.render(<Harness frameRef={{ current: frame }} readerDriven
      pagination={{ hasMore: true, loading: false, loadEarlier }} />));
    await act(async () => { viewport.dispatchEvent(new Event("scroll")); });
    expect(loadEarlier).not.toHaveBeenCalled();
    await act(async () => { viewport.dispatchEvent(new WheelEvent("wheel", { deltaY: -50 })); });
    expect(loadEarlier).toHaveBeenCalledTimes(1);
    frame.remove();
  });
});

describe("composer pinned to the bottom", () => {
  it("keeps the thread grow div on the assistant surface", () => {
    const source = readFileSync(join(process.cwd(), "src/components/conversation-thread.tsx"), "utf8");
    // 助手面曾经关掉 grow div（`unstable_flexGrowDiv={!assistant}`），对话短时输入框
    // 停在中段；加载态也一直悬着。现在所有面都保留，输入框始终沉底。
    expect(source).toContain("<Thread.Messages unstable_flexGrowDiv ");
    expect(source).not.toContain("unstable_flexGrowDiv={!assistant}");
  });
});
