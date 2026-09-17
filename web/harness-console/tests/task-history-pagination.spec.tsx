// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import {
  createThreadHistoryAdapter,
  invalidateThreadHistory,
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
});
