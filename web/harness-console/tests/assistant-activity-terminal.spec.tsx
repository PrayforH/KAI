import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  isTransientProgress,
  progressPhrase,
  shouldShowChatActivityRow,
} from "../src/components/grok-activity";

const thread = readFileSync(join(process.cwd(), "src/components/agent-thread.tsx"), "utf8");
const progress = readFileSync(join(process.cwd(), "src/components/assistant-progress.tsx"), "utf8");
const tail = readFileSync(join(process.cwd(), "src/components/assistant-conversation-tail.tsx"), "utf8");

const waiting = { items: [{ event_type: "runtime.system", summary: "正在等待本轮模型结果" }] };
const tool = { items: [{ event_type: "tool.result", summary: "已读取工作区文件" }] };

describe("terminal turns never keep a waiting row", () => {
  it("hides the row once the run succeeded (the reported pending_input delegation turn)", () => {
    expect(shouldShowChatActivityRow("succeeded", waiting)).toBe(false);
    expect(shouldShowChatActivityRow("completed", waiting)).toBe(false);
    expect(shouldShowChatActivityRow("failed", waiting)).toBe(false);
    expect(shouldShowChatActivityRow("cancelled", waiting)).toBe(false);
  });

  it("still shows real progress while the run is in flight", () => {
    expect(shouldShowChatActivityRow("running", waiting)).toBe(true);
    expect(shouldShowChatActivityRow("queued", waiting)).toBe(true);
    expect(shouldShowChatActivityRow("waiting_approval", tool)).toBe(true);
  });

  it("treats the waiting phrase as transient and can drop it entirely", () => {
    expect(isTransientProgress("正在等待本轮模型结果")).toBe(true);
    expect(isTransientProgress("已读取工作区文件")).toBe(false);
    expect(progressPhrase(waiting)).toBe("正在处理你的请求。");
    expect(progressPhrase(waiting, { allowTransient: false })).toBe("");
    // 真实进展在终态过滤下仍然保留（用于正常进行中的行）。
    expect(progressPhrase(tool, { allowTransient: false })).toBe("已读取工作区文件");
  });

  it("both chat-form activity rows are gated by the predicate", () => {
    // 行渲染点收敛到 AssistantProgress：状态不活跃就整行不渲染。
    expect(progress).toContain("const visible = shouldShowChatActivityRow(activity.status, activity);");
    expect(progress).toContain("if (!visible) return null;");
    // 助手面的实时行与 composer 旁的 tail 都走同一个组件。
    expect(tail).toContain("<AssistantProgress activity={activity} avatar={avatar} compact");
    // 任务面（非助手面）的 pre-response 行保留原有入口。
    expect(thread).toContain("{preResponseActivity && !assistantSurface ? (");
  });

  it("keeps the duplicate-answer guards from dce054a7", () => {
    expect(progressPhrase({ items: [{ event_type: "message.delta", summary: "正文" }] })).toBe("");
    expect(thread).toContain("suppressText={Boolean(copyText.trim())}");
  });
});

describe("live status column", () => {
  it("shares the composer's centered column instead of the full viewport width", () => {
    const css = readFileSync(join(process.cwd(), "src/app/assistant-conversation.css"), "utf8");
    const rule = css.slice(css.indexOf(".assistant-message-tail-slot"));
    const block = rule.slice(0, rule.indexOf("}"));
    // 输入框列是 width:min(800px,100%) + margin-inline:auto；状态行必须同列，
    // 否则窗口一宽就贴到最左边。
    expect(block).toContain("min(800px, 100%)");
    expect(block).toContain("margin-inline: auto");
    expect(block).not.toMatch(/width:\s*100%/);
  });
});
