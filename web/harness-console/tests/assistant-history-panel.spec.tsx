// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";

const history = vi.fn();
vi.mock("../src/lib/task-history", () => ({ loadFullThreadHistory: (...args: unknown[]) => history(...args) }));
vi.mock("../src/lib/client-auth", () => ({ requireAuthenticatedResponse: (value: unknown) => value }));
import {
  AssistantHistoryPanel, fileGlyph, fileKindOf, filterFiles, historyEntries,
} from "../src/components/assistant-history-panel";
import { clearHistoryIndex, readHistoryIndex } from "../src/lib/history-index";
import { flashMessage, registerHistoryLocator } from "../src/lib/chat-locate";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const host = document.createElement("div");
document.body.append(host);
let root = createRoot(host);
const artifacts = [{
  artifact_id: "artifact_1", run_id: "run_1", thread_id: "thread_1",
  name: "report.html", media_type: "text/html", size_bytes: 2048,
  created_at: "2026-11-10T09:00:00Z",
}];
afterEach(() => {
  act(() => root.unmount());
  host.replaceChildren();
  root = createRoot(host);
  history.mockReset();
  clearHistoryIndex();
  registerHistoryLocator(null);
  document.querySelectorAll("[data-message-id]").forEach(node => node.remove());
  vi.unstubAllGlobals();
});

function stubFetch() {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => artifacts })));
}

async function render() {
  history.mockResolvedValue([
    { id: "user-run_1", role: "user", content: "帮我看下报告" },
    { id: "assistant-run_1", role: "assistant", content: "报告已生成" },
  ]);
  stubFetch();
  await act(async () => { root.render(<AssistantHistoryPanel open threadId="thread_1" assistantName="小树" onClose={() => {}} />); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 10)); });
}

async function waitForText(text: string, timeoutMs = 3_000) {
  const deadline = Date.now() + timeoutMs;
  while (!host.textContent?.includes(text)) {
    if (Date.now() >= deadline) throw new Error(`did not render: ${text}`);
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
  }
}

async function click(text: string) {
  await act(async () => {
    const button = [...host.querySelectorAll("button")].find(item => item.textContent === text);
    button!.click();
  });
}

it("labels file kinds like the prototype glyphs", () => {
  expect(fileKindOf("a.PNG", "image/png")).toBe("image");
  expect(fileKindOf("clip.mp4", "")).toBe("video");
  expect(fileKindOf("deck.pdf", "application/pdf")).toBe("pdf");
  expect(fileKindOf("page.htm", "text/html")).toBe("html");
  expect(fileKindOf("bundle.tgz", "")).toBe("zip");
  expect(fileGlyph("report.html", "text/html")).toBe("HTML");
  expect(fileGlyph("notes.md", "")).toBe("MD");
});

it("attaches produced files to the assistant message that owns the run", () => {
  const { entries, files } = historyEntries([
    { id: "user-run_1", role: "user", content: "帮我总结" },
    {
      id: "assistant-run_1", role: "assistant", content: [
        { type: "text", text: "好了" },
        { type: "document", source: { value: "input_7" }, metadata: { filename: "需求.pdf" } },
      ],
    },
  ], [{ artifact_id: "artifact_1", run_id: "run_1", name: "report.html", media_type: "text/html" }]);
  expect(entries[1].files.map(file => file.name)).toEqual(["需求.pdf", "report.html"]);
  expect(files.find(file => file.name === "report.html")?.messageId).toBe("assistant-run_1");
  expect(files.find(file => file.name === "需求.pdf")?.origin).toBe("user");
  expect(files.find(file => file.name === "需求.pdf")?.inlineHref).toContain("/api/input-artifacts/input_7/content");
});

it("filters files by name, sender, kind and date", () => {
  const files = historyEntries([], [
    { artifact_id: "a", run_id: "r", name: "报告.pdf", media_type: "application/pdf", created_at: "2026-11-09T10:00:00Z" },
    { artifact_id: "b", run_id: "r", name: "demo.mp4", media_type: "video/mp4", created_at: "2026-11-10T10:00:00Z" },
  ]).files;
  expect(filterFiles(files, { query: "demo", sender: "all" }).map(file => file.name)).toEqual(["demo.mp4"]);
  expect(filterFiles(files, { query: "", sender: "all", kind: "video" }).map(file => file.name)).toEqual(["demo.mp4"]);
  expect(filterFiles(files, { query: "", sender: "assistant", date: "2026/11/09" }).map(file => file.name)).toEqual(["报告.pdf"]);
  expect(filterFiles(files, { query: "", sender: "user" })).toEqual([]);
});

it("lists messages and files, and locates a row back in the chat", async () => {
  await render();
  expect(host.textContent).toContain("帮我看下报告");
  expect(host.textContent).toContain("报告已生成");

  const target = document.createElement("div");
  target.setAttribute("data-message-id", "assistant-run_1");
  target.scrollIntoView = vi.fn();
  document.body.append(target);

  await click("文件");
  expect(host.textContent).toContain("report.html");
  expect(host.textContent).toContain("2.0 KB");

  await click("在聊天中定位");
  expect(target.scrollIntoView).toHaveBeenCalled();
  expect(target.classList.contains("assistant-history-located")).toBe(true);
});

it("searches file names and keeps media search disabled", async () => {
  await render();
  await click("文件");
  const search = [...host.querySelectorAll("input")].find(item => item.getAttribute("aria-label") === "搜索聊天记录")!;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    setter.call(search, "report");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect(host.textContent).toContain("report.html");

  await click("图片与视频");
  expect(host.textContent).toContain("暂无图片或视频");
  const mediaSearch = [...host.querySelectorAll("input")].find(item => item.getAttribute("aria-label") === "搜索聊天记录") as HTMLInputElement;
  expect(mediaSearch.disabled).toBe(true);
});

it("returns false when the message is not in the chat", () => {
  expect(flashMessage("missing-run")).toBe(false);
});

it("skips tool traces the conversation never mounts", () => {
  const { entries } = historyEntries([
    { id: "user-run_1", role: "user", content: "问题" },
    { id: "assistant-run_1", role: "assistant", content: "回答" },
    { id: "source-result-run_1", role: "tool", content: '{"status":"ready"}' },
  ], []);
  expect(entries.map(entry => entry.id)).toEqual(["user-run_1", "assistant-run_1"]);
});

it("paints the newest page before earlier pages finish, oldest first", async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  history.mockImplementation(async (_threadId: string, options: { onPage?: (page: unknown) => void }) => {
    options.onPage?.({ messages: [{ id: "user-run_new", role: "user", content: "最新消息" }] });
    await gate;
    options.onPage?.({ messages: [{ id: "user-run_old", role: "user", content: "更早消息" }] });
    return [];
  });
  stubFetch();
  await act(async () => { root.render(<AssistantHistoryPanel open threadId="thread_1" assistantName="小树" onClose={() => {}} />); });
  await act(async () => { await Promise.resolve(); });
  expect(host.textContent).toContain("最新消息");
  expect(host.textContent).not.toContain("更早消息");

  await act(async () => { release(); await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(host.textContent).toContain("更早消息");
  expect(host.textContent!.indexOf("更早消息")).toBeLessThan(host.textContent!.indexOf("最新消息"));
});

it("reuses a fresh index when the drawer is reopened", async () => {
  await render();
  const loads = history.mock.calls.length;

  act(() => root.unmount());
  root = createRoot(host);
  await act(async () => {
    root.render(<AssistantHistoryPanel open threadId="thread_1" assistantName="小树" onClose={() => {}} />);
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  expect(history).toHaveBeenCalledTimes(loads);
  expect(host.textContent).toContain("帮我看下报告");
});

it("names a failed file list instead of blaming older history", async () => {
  history.mockResolvedValue([{ id: "user-run_1", role: "user", content: "消息还在" }]);
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, json: async () => [] })));
  await act(async () => {
    root.render(<AssistantHistoryPanel open threadId="thread_1" assistantName="小树" onClose={() => {}} />);
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  expect(host.textContent).toContain("消息还在");
  expect(host.textContent).toContain("文件列表暂时无法读取，聊天记录仍可查看");
  expect(host.textContent).not.toContain("更早的记录暂时无法读取");
  // A half index (no files) must not be cached for the next open.
  expect(readHistoryIndex("thread_1")).toBeNull();
});

it("aborts an in-flight load when the drawer closes and caches nothing", async () => {
  let signal: AbortSignal | undefined;
  history.mockImplementation(((_threadId: string, options: { signal?: AbortSignal }) => {
    signal = options.signal;
    return new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        reject(new DOMException("aborted", "AbortError"));
      });
    });
  }) as never);
  stubFetch();
  await act(async () => {
    root.render(<AssistantHistoryPanel open threadId="thread_1" assistantName="小树" onClose={() => {}} />);
    await Promise.resolve();
  });
  expect(signal?.aborted).toBe(false);

  await act(async () => {
    root.render(<AssistantHistoryPanel open={false} threadId="thread_1" assistantName="小树" onClose={() => {}} />);
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  expect(signal?.aborted).toBe(true);
  expect(readHistoryIndex("thread_1")).toBeNull();
});

it("asks the reader to retry instead of claiming a message is missing while history loads", async () => {
  await render();
  // A mounted locator whose frame is gone means "cannot check yet", not "absent".
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => true,
    loadEarlier: async () => "exhausted" as const,
    frame: () => null,
  });
  await click("在聊天中定位");
  // A remounting frame is waited for briefly, so the notice lands asynchronously.
  await waitForText("聊天记录仍在加载，请稍后重试");
  expect(host.textContent).not.toContain("未在当前会话中找到这条消息");
});

it("hides the loading hint when a filter has no matches", async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  history.mockImplementation(async (_threadId: string, options: { onPage?: (page: unknown) => void }) => {
    options.onPage?.({ messages: [{ id: "user-run_1", role: "user", content: "唯一一条记录" }] });
    await gate;
    return [];
  });
  stubFetch();
  await act(async () => {
    root.render(<AssistantHistoryPanel open threadId="thread_1" assistantName="小树" onClose={() => {}} />);
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  expect(host.textContent).toContain("正在读取更早的记录");

  const search = [...host.querySelectorAll("input")].find(item => item.getAttribute("aria-label") === "搜索聊天记录") as HTMLInputElement;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")!.set!;
    setter.call(search, "没有这条");
    search.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise(resolve => setTimeout(resolve, 160));
  });
  expect(host.textContent).toContain("没有符合筛选条件的记录");
  expect(host.textContent).not.toContain("正在读取更早的记录");
  await act(async () => { release(); await new Promise(resolve => setTimeout(resolve, 10)); });
});

it("keeps the reader's place when an older page prepends above them", async () => {
  const gates: Array<() => void> = [];
  history.mockImplementation(async (_threadId: string, options: { onPage?: (page: unknown) => void }) => {
    options.onPage?.({ messages: [{ id: "user-new", role: "user", content: "最新" }] });
    await new Promise<void>(resolve => gates.push(resolve));
    options.onPage?.({ messages: [{ id: "user-old", role: "user", content: "较早" }] });
    await new Promise<void>(resolve => gates.push(resolve));
    options.onPage?.({ messages: [{ id: "user-oldest", role: "user", content: "最早" }] });
    return [];
  });
  stubFetch();
  await act(async () => {
    root.render(<AssistantHistoryPanel open threadId="thread_1" assistantName="小树" onClose={() => {}} />);
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  const body = host.querySelector("article")?.parentElement as HTMLDivElement | null;
  expect(body).not.toBeNull();
  let height = 1_200;
  Object.defineProperty(body!, "scrollHeight", { configurable: true, get: () => height });
  Object.defineProperty(body!, "clientHeight", { configurable: true, get: () => 500 });

  // The first page fills in: still pinned to the newest message.
  await act(async () => { gates.shift()!(); await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(body!.scrollTop).toBe(1_200);

  // The reader scrolls up, away from the bottom.
  await act(async () => {
    body!.scrollTop = 200;
    body!.dispatchEvent(new Event("scroll"));
  });

  // An older page arrives above the viewport; the offset must absorb it.
  height = 1_600;
  await act(async () => { gates.shift()!(); await new Promise(resolve => setTimeout(resolve, 10)); });
  expect(body!.scrollTop).toBe(600);
});
