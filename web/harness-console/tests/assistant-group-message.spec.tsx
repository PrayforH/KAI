// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupMessage, GROUP_REACTIONS } from "../src/components/assistant-group-message";
import type { GroupMessage } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function message(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    messageId: "m1",
    groupId: "g1",
    author: "specialist",
    authorName: "Agent 内核",
    specialistId: "kernel",
    text: "收到",
    runId: null,
    replyToMessageId: null,
    reactions: [],
    status: "completed",
    createdAt: "2026-11-13T05:00:00Z",
    ...overrides,
  };
}

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
  vi.unstubAllGlobals();
});

async function render(node: ReactNode) {
  await act(async () => root!.render(node));
}

/** 让事件流的微任务与状态更新落地。 */
async function flush() {
  await act(async () => {
    await new Promise((done) => setTimeout(done, 0));
    await new Promise((done) => setTimeout(done, 0));
  });
}

function frame(event: unknown) {
  return `data: ${JSON.stringify(event)}\n\n`;
}

function sseBody(chunks: string[]) {
  const encoder = new TextEncoder();
  let index = 0;
  return {
    getReader() {
      return {
        read: async () =>
          index < chunks.length
            ? { value: encoder.encode(chunks[index++]), done: false }
            : { value: undefined, done: true },
        cancel: async () => undefined,
      };
    },
  };
}

describe("AssistantGroupMessage", () => {
  it("renders bot markdown without a bubble", async () => {
    await render(
      <AssistantGroupMessage
        message={message({
          text: "## 结论\n\n- 第一点\n- 第二点\n\n| 项 | 值 |\n|---|---|\n| A | 1 |\n\n```ts\nconst a = 1;\n```",
        })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector("h1, h2, h3")?.textContent).toContain("结论");
    expect(host.querySelectorAll("li")).toHaveLength(2);
    expect(host.querySelector("table")).not.toBeNull();
    expect(host.querySelector("pre code")?.textContent).toContain("const a = 1");
  });

  it("renders a mention as a chip", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ text: "请 @产品规划 看一下" })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const chip = host.querySelector("[data-mention]");
    expect(chip?.textContent).toBe("@产品规划");
  });

  it("keeps user messages on the right as a bubble", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ author: "user", authorName: "我", text: "**加粗**" })}
        tone="#6b746e"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const article = host.querySelector('[data-author="user"]');
    expect(article).not.toBeNull();
    expect(article?.querySelector("strong")?.textContent).toBe("加粗");
  });

  it("renders system notices centred and without actions", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ author: "system", authorName: "系统", text: "成员已加入" })}
        tone="#6b746e"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const article = host.querySelector('[data-author="system"]');
    expect(article?.textContent).toContain("成员已加入");
    expect(article?.querySelector("button")).toBeNull();
  });

  it("shows the running placeholder for an empty reply", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    await render(
      <AssistantGroupMessage
        message={message({ text: "", status: "running" })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    // 没有 runId 就没有可跟随的流，不该发请求。
    expect(fetchMock).not.toHaveBeenCalled();
    expect(host.textContent).toContain("正在回复…");
  });

  it("shows real progress stages from the run event stream", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      body: sseBody([
        frame({
          type: "ACTIVITY_SNAPSHOT",
          activityType: "harness.run.v1",
          content: {
            run_id: "run-1",
            status: "running",
            started_at: "2026-11-17T02:00:00Z",
            items: [{ id: "1", event_type: "run.started", metadata: {} }],
            metrics: {},
          },
        }),
        frame({
          type: "ACTIVITY_DELTA",
          activityType: "harness.run.v1",
          patch: [{ op: "add", path: "/items/-", value: { event_type: "tool.request", metadata: { name: "assistant_group_update_profile" } } }],
        }),
        frame({
          type: "ACTIVITY_DELTA",
          activityType: "harness.run.v1",
          patch: [{ op: "add", path: "/items/-", value: { event_type: "tool.result", metadata: {} } }],
        }),
        frame({
          type: "ACTIVITY_DELTA",
          activityType: "harness.run.v1",
          patch: [{ op: "add", path: "/items/-", value: { event_type: "message.delta", summary: "好的" } }],
        }),
        frame({ type: "RUN_FINISHED" }),
      ]),
    })));
    await render(
      <AssistantGroupMessage
        message={message({ text: "", status: "running", runId: "run-1" })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    await flush();
    expect(host.textContent).toContain("正在整理回复");
    // 阶段文案不得泄露工具名、事件名或内部 id。
    expect(host.textContent).not.toContain("assistant_group_update_profile");
    expect(host.textContent).not.toContain("tool.request");
    expect(host.textContent).not.toContain("run-1");
    const status = host.querySelector('[role="status"]');
    expect(status?.className).toContain("running");
    expect(status?.getAttribute("aria-live")).toBe("polite");
  });

  it("keeps the tool stage while a parallel tool call is still running", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      body: sseBody([
        frame({ type: "TOOL_CALL_START", toolCallId: "a" }),
        frame({ type: "TOOL_CALL_END", toolCallId: "a" }),
        frame({ type: "TOOL_CALL_START", toolCallId: "b" }),
        frame({ type: "TOOL_CALL_RESULT", toolCallId: "a" }),
      ]),
    })));
    await render(
      <AssistantGroupMessage
        message={message({ text: "", status: "running", runId: "run-1" })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    await flush();
    expect(host.textContent).toContain("正在调用工具");
  });

  it("follows the new runId when an automatic retry replaces the run", async () => {
    const fetchMock = vi.fn(async (url: string) => ({
      ok: true,
      body: sseBody(
        String(url).includes("run-2")
          ? [
              frame({
                type: "ACTIVITY_SNAPSHOT",
                activityType: "harness.run.v1",
                content: {
                  items: [
                    { id: "1", event_type: "tool.request", metadata: { tool_call_id: "a" } },
                  ],
                },
              }),
              frame({ type: "RUN_FINISHED" }),
            ]
          : [frame({ type: "RUN_STARTED" }), frame({ type: "RUN_FINISHED" })],
      ),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const props = {
      tone: "#38d591",
      onReact: vi.fn(),
      onReply: vi.fn(),
    };
    await render(
      <AssistantGroupMessage
        message={message({ text: "", status: "running", runId: "run-1" })}
        {...props}
      />,
    );
    await flush();
    await render(
      <AssistantGroupMessage
        message={message({ text: "", status: "running", runId: "run-2" })}
        {...props}
      />,
    );
    await flush();
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls).toContain("/api/agui/runs/run-2/events");
    expect(host.textContent).toContain("正在调用工具");
  });

  it("falls back to polling text when the event stream is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("offline");
    }));
    await render(
      <AssistantGroupMessage
        message={message({ text: "", status: "running", runId: "run-1" })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    await flush();
    expect(host.textContent).toContain("正在回复…");
  });

  it("keeps the progress row stable and honours reduced motion", () => {
    const css = readFileSync(
      resolve(process.cwd(), "src/components/assistant-group-message.module.css"),
      "utf8",
    );
    const placeholder = css.match(/\.placeholder\s*\{([^}]*)\}/)?.[1] ?? "";
    const running = css.match(/\.running\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(placeholder).toContain("line-height: 1.6");
    expect(running).toContain("animation: groupStatusSweep 2.4s linear infinite");
    expect(running).toContain("background-clip: text");
    // 减弱动效 / 强制配色下必须能读，且没有动画。
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\), \(forced-colors: active\)/);
    expect(css).toMatch(/\.running \{[^}]*animation: none/s);
  });

  it("renders the quote snapshot when replyTo is present", async () => {
    await render(
      <AssistantGroupMessage
        message={message({
          replyToMessageId: "m0",
          replyTo: {
            messageId: "m0",
            author: "user",
            authorName: "我",
            text: "上一句问题",
          },
        })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector('[aria-label="引用 我"]')?.textContent).toContain("上一句问题");
  });

  it("renders no quote block when replyTo is omitted", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ replyToMessageId: null })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector('[aria-label^="引用 "]')).toBeNull();
  });

  it("opens the five-key picker and reports a reaction", async () => {
    const onReact = vi.fn();
    await render(
      <AssistantGroupMessage
        message={message()}
        tone="#38d591"
        onReact={onReact}
        onReply={vi.fn()}
      />,
    );
    const trigger = host.querySelector<HTMLButtonElement>('[aria-label="给这条消息加表情"]')!;
    await act(async () => trigger.click());
    const picker = host.querySelector('[aria-label="选择表情"]')!;
    expect(picker.querySelectorAll("button")).toHaveLength(GROUP_REACTIONS.length);
    await act(async () =>
      picker.querySelector<HTMLButtonElement>("button")!.click(),
    );
    expect(onReact).toHaveBeenCalledWith(expect.objectContaining({ messageId: "m1" }), GROUP_REACTIONS[0]);
  });

  it("does not offer reactions on my own sent message", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ author: "user", authorName: "我", reactions: ["👍"] })}
        tone="#6b746e"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector('[aria-label="给这条消息加表情"]')).toBeNull();
    expect(host.querySelector('[aria-pressed="true"]')).toBeNull();
  });

  it("cancels a selected reaction chip", async () => {
    const onReact = vi.fn();
    await render(
      <AssistantGroupMessage
        message={message({ reactions: ["👍"] })}
        tone="#38d591"
        onReact={onReact}
        onReply={vi.fn()}
      />,
    );
    const chip = host.querySelector<HTMLButtonElement>('[aria-pressed="true"]')!;
    expect(chip.textContent).toBe("👍");
    await act(async () => chip.click());
    expect(onReact).toHaveBeenCalledWith(expect.anything(), "👍");
  });

  it("never renders a single-message delete action", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ author: "user", authorName: "我" })}
        tone="#6b746e"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector('[aria-label="删除这条消息"]')).toBeNull();
    expect(host.querySelector('[aria-label="确认删除这条消息"]')).toBeNull();
    // 引用与复制仍在，只有删除被统一取消。
    expect(host.querySelector('[aria-label="复制这条消息"]')).not.toBeNull();
  });

  it("renders attachments as downloadable chips", async () => {
    await render(
      <AssistantGroupMessage
        message={message({
          attachments: [{ documentId: "d1", name: "需求.docx", downloadUrl: "/download/d1" }],
        })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const link = host.querySelector<HTMLAnchorElement>('[aria-label="附件"] a')!;
    expect(link.textContent).toContain("需求.docx");
    expect(link.getAttribute("href")).toBe("/download/d1");
  });

  it("routes attachment downloads through the web /api prefix", async () => {
    await render(
      <AssistantGroupMessage
        message={message({
          attachments: [
            {
              documentId: "d1",
              name: "需求.docx",
              downloadUrl: "/v1/studio/assistant/groups/g1/library/documents/d1/content",
            },
          ],
        })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const link = host.querySelector<HTMLAnchorElement>('[aria-label="附件"] a')!;
    expect(link.getAttribute("href")).toBe(
      "/api/studio/assistant/groups/g1/library/documents/d1/content",
    );
  });

  it("renders a non-clickable chip when an attachment has no download url", async () => {
    await render(
      <AssistantGroupMessage
        message={message({
          attachments: [{ documentId: "d1", name: "需求.docx" }],
        })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector('[aria-label="附件"] a')).toBeNull();
    expect(host.querySelector('[aria-label="附件"]')?.textContent).toContain("需求.docx");
  });

  it("reports a reply request", async () => {
    const onReply = vi.fn();
    await render(
      <AssistantGroupMessage
        message={message()}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={onReply}
      />,
    );
    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label="引用这条消息"]')!.click(),
    );
    expect(onReply).toHaveBeenCalledWith(expect.objectContaining({ messageId: "m1" }));
  });

  it("gives the user column a definite width so short messages stay on one line", () => {
    // jsdom 不做布局：守住「百分比 max-width 有确定参照物」的前提。
    // 列必须是满宽的 flex item，气泡的 78% 才会相对整行结算，而不是相对自身 max-content。
    // 真实渲染宽度见 diag/bubble-wrap-20261116/render-measure-*.log。
    const css = readFileSync(
      resolve(process.cwd(), "src/components/assistant-group-message.module.css"),
      "utf8",
    );
    const columnBlock = css.match(/\.userColumn\s*\{([^}]*)\}/)?.[1] ?? "";
    const bubbleBlock = css.match(/\.userBubble\s*\{([^}]*)\}/)?.[1] ?? "";
    const quotedBlock = css.match(/\.userColumn \.quoted\s*\{([^}]*)\}/)?.[1] ?? "";
    const attachmentsBlock = css.match(/\.userColumn \.attachments\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(columnBlock).toContain("width: 100%");
    expect(columnBlock).toContain("align-items: flex-end");
    // 气泡本身仍按内容收缩，只封顶，不铺满。
    expect(bubbleBlock).toContain("width: max-content");
    expect(bubbleBlock).toContain("max-width: min(640px, 78%)");
    // 引用条和附件跟着气泡的宽度上限，不能横贯整行。
    expect(quotedBlock).toContain("align-self: flex-end");
    expect(quotedBlock).not.toContain("align-self: stretch");
    expect(quotedBlock).toContain("max-width: min(640px, 78%)");
    expect(attachmentsBlock).toContain("max-width: min(640px, 78%)");
  });
});
