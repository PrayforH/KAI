// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantProgressStrip, progressNoticeLink } from "../src/components/assistant-progress-strip";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const notices = [
  {
    itemId: "f1",
    objectiveId: "o1",
    title: "沙箱失败",
    content: "工具执行失败",
    kind: "failed",
    createdAt: "2026-11-01T01:00:00Z",
    readAt: null,
    threadId: "assistant_o1",
    runId: "run-1",
    executionId: "execution-1",
  },
  {
    itemId: "s1",
    objectiveId: "o2",
    title: "行业快报",
    content: "已生成第一期",
    kind: "succeeded",
    createdAt: "2026-11-01T02:00:00Z",
    readAt: null,
    threadId: "assistant_o2",
    runId: "run-2",
    executionId: "execution-2",
  },
  {
    itemId: "c1",
    objectiveId: "o3",
    title: "手动停止",
    content: "已按你的要求停止",
    kind: "cancelled",
    createdAt: "2026-11-01T03:00:00Z",
    readAt: null,
    threadId: "assistant_o3",
  },
  {
    itemId: "f2",
    objectiveId: "o4",
    title: "旧失败",
    content: "历史载荷没有 run",
    kind: "failed",
    createdAt: "2026-11-01T04:00:00Z",
    readAt: null,
    threadId: "assistant_o4",
  },
];

let root: Root;
let host: HTMLDivElement;
const fetcher = vi.fn();

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  fetcher.mockReset();
  fetcher.mockImplementation(async () => Response.json(notices));
  vi.stubGlobal("fetch", fetcher);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function mountExpanded() {
  await act(async () => root.render(<AssistantProgressStrip />));
  await act(async () => { await Promise.resolve(); });
  const toggle = host.querySelector<HTMLButtonElement>(".assistant-progress-toggle");
  await act(async () => toggle!.click());
}

function linkFor(title: string): HTMLAnchorElement | null {
  const row = [...host.querySelectorAll(".assistant-progress-strip li")].find((node) =>
    (node.textContent ?? "").includes(title),
  );
  return row?.querySelector<HTMLAnchorElement>(".assistant-progress-link") ?? null;
}

describe("progress notice replay link", () => {
  it("opens a failed notice at the run checkpoint with trace", async () => {
    await mountExpanded();
    const link = linkFor("沙箱失败");
    expect(link?.textContent).toBe("打开失败回放 →");
    expect(link?.getAttribute("data-replay")).toBe("failure");
    const params = new URL(link!.href).searchParams;
    expect(params.get("thread")).toBe("assistant_o1");
    expect(params.get("trace")).toBe("1");
    expect(params.get("run")).toBe("run-1");
    expect(params.get("checkpoint")).toBe("checkpoint_execution-1");
  });

  it("keeps the original run on result links without opening failure trace", async () => {
    await mountExpanded();
    const succeeded = linkFor("行业快报");
    expect(succeeded?.textContent).toBe("打开原任务 →");
    expect(succeeded?.getAttribute("data-replay")).toBe("thread");
    const params = new URL(succeeded!.href).searchParams;
    expect(params.get("thread")).toBe("assistant_o2");
    expect(params.has("trace")).toBe(false);
    expect(params.get("run")).toBe("run-2");
    expect(params.has("checkpoint")).toBe(false);

    const cancelled = linkFor("手动停止");
    expect(cancelled?.textContent).toBe("打开原任务 →");
    expect(new URL(cancelled!.href).searchParams.has("trace")).toBe(false);
  });

  it("falls back to trace-only replay for legacy failed notices", async () => {
    await mountExpanded();
    const link = linkFor("旧失败");
    expect(link?.textContent).toBe("打开失败回放 →");
    const params = new URL(link!.href).searchParams;
    expect(params.get("thread")).toBe("assistant_o4");
    expect(params.get("trace")).toBe("1");
    expect(params.has("run")).toBe(false);
    expect(params.has("checkpoint")).toBe(false);
  });
});

describe("progressNoticeLink helper", () => {
  it("never fabricates run ids and returns null without a thread", () => {
    expect(progressNoticeLink({ kind: "failed", threadId: null })).toBeNull();
    const legacy = progressNoticeLink({ kind: "failed", threadId: "t", runId: null, executionId: null });
    expect(legacy?.href).toBe("/?thread=t&trace=1");
    const measured = progressNoticeLink({ kind: "succeeded", threadId: "t", runId: "r" });
    expect(measured?.href).toBe("/?thread=t&run=r");
  });
});

it("returns a standalone Bot result to its dedicated conversation", () => {
  const link = progressNoticeLink({
    kind: "succeeded", threadId: "assistant_specialist_coding", runId: "run-result",
  });
  expect(link?.href).toBe("/assistant?bot=specialist_coding&run=run-result");
});
