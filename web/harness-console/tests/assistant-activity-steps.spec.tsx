// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  ChatActivitySteps,
  GrokActivityRow,
  activitySteps,
  progressPhrase,
  shouldShowChatActivityRow,
  shouldShowChatActivitySteps,
  trimConclusion,
  type ActivityLike,
} from "../src/components/grok-activity";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function item(event_type: string, fields: Partial<ActivityLike> = {}): ActivityLike {
  return { event_type, title: "", summary: null, status: "succeeded", metadata: {}, ...fields };
}

const progressNote = item("message.delta", {
  id: "n1",
  title: "进展说明",
  summary: "收到快捷操作：目标改为 8 公里。先确认参数。",
});
const appliedNote = item("message.delta", {
  id: "n2",
  title: "进展说明",
  summary: "参数已确认：当前目标 8 公里，现在改目标距离为 8000 米。",
});
const finalAnswer = item("message.delta", {
  id: "final",
  title: "进展说明",
  summary: "已改好。".repeat(80), // 远超单条结论上限，代表终答
});
const toolRequest = item("tool.request", {
  id: "t1",
  title: "调用 Bash",
  status: "running",
  metadata: { name: "Bash" },
});
const toolResult = item("tool.result", {
  id: "t2",
  title: "工具调用完成",
  metadata: { result_summary: "返回 12 行 · 340 字符" },
});

describe("progressPhrase prefers business conclusions", () => {
  it("uses the latest intermediate message.delta as the phrase", () => {
    const activity = { items: [progressNote, toolRequest, toolResult] };
    // 活动行只留极短结论（≤10 汉字宽），停在从句边界。
    expect(progressPhrase(activity)).toBe("收到快捷操作：");
  });

  it("takes the newest conclusion when there are several between tools", () => {
    const activity = { items: [progressNote, toolRequest, appliedNote, toolResult] };
    expect(progressPhrase(activity)).toBe("参数已确认：");
  });

  it("never uses the final answer that follows the last tool", () => {
    const activity = { items: [progressNote, toolRequest, appliedNote, toolResult, finalAnswer] };
    expect(progressPhrase(activity)).toBe("参数已确认：");
  });

  it("does not treat tool guidance as the main phrase", () => {
    const activity = {
      items: [
        toolRequest,
        item("runtime.system", { title: "工具能力", summary: "14 项工具可用" }),
        item("runtime.system", { title: "运行环境", summary: "运行环境已就绪" }),
      ],
    };
    expect(progressPhrase(activity)).toBe("");
  });

  it("falls back to one non-guidance tool result when no conclusion exists", () => {
    const activity = { items: [toolRequest, toolResult] };
    expect(progressPhrase(activity)).toBe("返回 12 行 · 340…");
  });

  it("keeps the waiting phrase only as a fallback while nothing better is known", () => {
    const activity = {
      items: [item("runtime.system", { summary: "正在等待本轮模型结果", status: "running" })],
    };
    expect(progressPhrase(activity)).toBe("正在等待本轮模型结果");
    expect(progressPhrase(activity, { allowTransient: false })).toBe("");
  });
});

describe("assistant activity steps list conclusions, not tool logs", () => {
  const activity = { items: [progressNote, toolRequest, appliedNote, toolResult] };

  it("lists the older conclusions and drops the phrase duplicate", () => {
    const phrase = progressPhrase(activity);
    const steps = activitySteps(activity, { excludeText: phrase });
    expect(steps.map((step) => step.text)).toEqual(["收到快捷操作："]);
    expect(steps.every((step) => !step.text.startsWith("调用"))).toBe(true);
    expect(steps.every((step) => step.text !== "工具调用完成")).toBe(true);
  });

  it("caps at five conclusions and dedupes identical text", () => {
    const notes = Array.from({ length: 8 }, (_, index) =>
      item("message.delta", { id: `n${index}`, summary: `第 ${index} 步结论。` }),
    );
    const steps = activitySteps({ items: [...notes, toolResult] });
    expect(steps).toHaveLength(5);
    expect(steps.map((step) => step.text)).toEqual([
      "第 3 步结论。",
      "第 4 步结论。",
      "第 5 步结论。",
      "第 6 步结论。",
      "第 7 步结论。",
    ]);
  });

  it("shows while running and hides once the turn is terminal", () => {
    expect(shouldShowChatActivitySteps("running", activity)).toBe(true);
    expect(shouldShowChatActivitySteps("waiting_approval", activity)).toBe(true);
    expect(shouldShowChatActivitySteps("completed", activity)).toBe(false);
    expect(shouldShowChatActivitySteps("succeeded", activity)).toBe(false);
    expect(shouldShowChatActivitySteps("failed", activity)).toBe(false);
    // 0ee6fffd 的规则不回退：终态活动行本身也不渲染。
    expect(shouldShowChatActivityRow("succeeded", activity)).toBe(false);
  });

  it("has no steps when the turn only produced tool logs", () => {
    const toolsOnly = { items: [toolRequest, toolResult] };
    expect(activitySteps(toolsOnly)).toEqual([]);
    expect(shouldShowChatActivitySteps("running", toolsOnly)).toBe(false);
  });
});

function displayWidth(text: string): number {
  return [...text].reduce((sum, ch) => sum + (/^[\x00-\x7F]$/.test(ch) ? 0.5 : 1), 0);
}

describe("conclusion trimming stays inside ten hanzi", () => {
  it("keeps a short conclusion intact", () => {
    expect(trimConclusion("参数已确认。")).toBe("参数已确认。");
  });

  it("stops long Chinese at a clause boundary inside the budget", () => {
    expect(trimConclusion("收到快捷操作：目标改为 8 公里。先确认参数。")).toBe("收到快捷操作：");
    expect(trimConclusion("第一句结论。".repeat(20))).toBe("第一句结论。");
  });

  it("hard-trims to nine hanzi plus an ellipsis when no boundary fits", () => {
    const trimmed = trimConclusion("很长的进展说明文字".repeat(5));
    expect(trimmed.endsWith("…")).toBe(true);
    expect(displayWidth(trimmed)).toBeLessThanOrEqual(10);
    expect([...trimmed]).toHaveLength(10); // 9 个汉字 + 省略号
  });

  it("allows roughly twenty ASCII characters for the same budget", () => {
    const trimmed = trimConclusion("Reading the two source files carefully");
    expect(trimmed.endsWith("…")).toBe(true);
    expect(trimmed.length).toBeLessThanOrEqual(20);
  });

  it("never returns a long paragraph as a conclusion", () => {
    const long = "我先盘点工作区里的文件，确认这次检查需要核对哪些材料。".repeat(4);
    expect(displayWidth(trimConclusion(long))).toBeLessThanOrEqual(10);
  });
});

describe("ChatActivitySteps rendering", () => {
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
  });

  it("renders conclusion lines only (no tool names)", async () => {
    const activity = { items: [progressNote, toolRequest, appliedNote, toolResult] };
    await act(async () => {
      root!.render(
        <ChatActivitySteps
          activity={activity}
          phrase={progressPhrase(activity)}
        />,
      );
    });
    const rows = [...host.querySelectorAll(".chat-activity-steps li")].map((n) => n.textContent);
    expect(rows).toEqual(["收到快捷操作："]);
    expect(host.textContent ?? "").not.toContain("调用 Bash");
    expect(host.querySelector(".execution-ribbon")).toBeNull();
  });

  it("caps the row phrase itself even if a caller passes a paragraph", async () => {
    await act(async () => {
      root!.render(
        <GrokActivityRow phrase={"这是一段很长的进展说明".repeat(6)} state="working" />,
      );
    });
    const text = host.querySelector(".grok-phrase")?.textContent ?? "";
    expect(displayWidth(text)).toBeLessThanOrEqual(10);
  });

  it("renders nothing when there is only a tool log", async () => {
    await act(async () => {
      root!.render(
        <ChatActivitySteps
          activity={{ items: [toolRequest, toolResult] }}
          phrase={progressPhrase({ items: [toolRequest, toolResult] })}
        />,
      );
    });
    expect(host.querySelector(".chat-activity-steps")).toBeNull();
  });
});

describe("assistant thread wiring", () => {
  it("renders the step list in both assistant activity branches", () => {
    const thread = readFileSync(
      join(process.cwd(), "src/components/agent-thread.tsx"),
      "utf8",
    );
    const matches = thread.match(/<ChatActivitySteps/g) ?? [];
    expect(matches).toHaveLength(2);
  });
});
