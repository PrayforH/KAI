// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  ChatActivitySteps,
  activitySteps,
  progressPhrase,
  shouldShowChatActivityRow,
  shouldShowChatActivitySteps,
  type ActivityLike,
} from "../src/components/grok-activity";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function item(event_type: string, fields: Partial<ActivityLike> = {}): ActivityLike {
  return { event_type, title: "", summary: null, status: "succeeded", metadata: {}, ...fields };
}

describe("progressPhrase uses titles for tool events", () => {
  it("returns the tool title when summary is null (tool.request)", () => {
    const activity = {
      items: [
        item("runtime.system", { summary: "正在等待本轮模型结果", status: "running" }),
        item("tool.request", { title: "调用 Shell", status: "running", metadata: { name: "Shell" } }),
      ],
    };
    expect(progressPhrase(activity)).toBe("调用 Shell");
  });

  it("takes the newest tool phrase once a tool call follows the waiting summary", () => {
    const activity = {
      items: [
        item("runtime.system", { summary: "正在等待本轮模型结果", status: "running" }),
        item("tool.request", { title: "调用 Shell", status: "running" }),
        item("tool.request", { title: "调用 read_file", status: "running" }),
      ],
    };
    expect(progressPhrase(activity)).toBe("调用 read_file");
  });

  it("prefers a short tool.result summary over the generic title", () => {
    const activity = {
      items: [
        item("tool.result", {
          title: "工具调用完成",
          metadata: { result_summary: "返回 12 行 · 340 字符" },
        }),
      ],
    };
    expect(progressPhrase(activity)).toBe("返回 12 行 · 340 字符");
  });

  it("still ignores prose, reasoning, run events and over-long text", () => {
    const long = "很长的进展文案".repeat(20);
    const activity = {
      items: [
        item("message.delta", { title: "进展说明", summary: "整段回答正文" }),
        item("reasoning.delta", { title: "思考", summary: "内部思考" }),
        item("run.started", { title: "运行开始" }),
        item("tool.request", { title: long, status: "running" }),
      ],
    };
    expect(progressPhrase(activity)).toBe("");
  });
});

describe("assistant activity steps", () => {
  const running = {
    items: [
      item("tool.request", { id: "a", title: "调用 Shell", status: "running" }),
      item("tool.result", {
        id: "b",
        title: "工具调用完成",
        metadata: { result_summary: "返回 12 行 · 340 字符" },
      }),
      item("runtime.system", { id: "c", summary: "正在等待本轮模型结果", status: "running" }),
    ],
  };

  it("builds a short recent-steps list and skips the phrase duplicate", () => {
    const phrase = progressPhrase(running); // 「正在等待本轮模型结果」
    const steps = activitySteps(running, { excludeText: phrase });
    expect(steps.map((step) => step.text)).toEqual([
      "调用 Shell",
      "返回 12 行 · 340 字符",
    ]);
  });

  it("drops transient waiting text and non-process events", () => {
    const steps = activitySteps({
      items: [
        item("runtime.system", { summary: "正在等待本轮模型结果", status: "running" }),
        item("message.delta", { summary: "正文", title: "进展说明" }),
        item("tool.request", { title: "调用 Shell", status: "running" }),
      ],
    });
    expect(steps.map((step) => step.text)).toEqual(["调用 Shell"]);
  });

  it("caps the list at five items and dedupes identical text", () => {
    const items = Array.from({ length: 8 }, (_, index) =>
      item("tool.request", { id: `t${index}`, title: `调用 tool_${index}`, status: "running" }),
    );
    items.push(item("tool.request", { id: "dup", title: "调用 tool_7", status: "running" }));
    const steps = activitySteps({ items });
    expect(steps).toHaveLength(5);
    expect(steps.at(-1)?.text).toBe("调用 tool_7");
    expect(new Set(steps.map((step) => step.text)).size).toBe(5);
  });

  it("marks failed steps for styling", () => {
    const steps = activitySteps({
      items: [item("tool.result", { title: "工具调用失败", status: "failed" })],
    });
    expect(steps[0]).toMatchObject({ text: "工具调用失败", tone: "failed" });
  });

  it("shows while running and hides once the turn is terminal", () => {
    expect(shouldShowChatActivitySteps("running", running)).toBe(true);
    expect(shouldShowChatActivitySteps("waiting_approval", running)).toBe(true);
    expect(shouldShowChatActivitySteps("completed", running)).toBe(false);
    expect(shouldShowChatActivitySteps("succeeded", running)).toBe(false);
    expect(shouldShowChatActivitySteps("failed", running)).toBe(false);
    // 0ee6fffd 的规则不回退：终态活动行本身也不渲染。
    expect(shouldShowChatActivityRow("succeeded", running)).toBe(false);
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

  it("renders one line per step, without any task-surface ribbon", async () => {
    const activity = {
      items: [
        item("tool.request", { id: "a", title: "调用 Shell", status: "running" }),
        item("tool.request", { id: "b", title: "调用 read_file", status: "running" }),
      ],
    };
    await act(async () => {
      root!.render(<ChatActivitySteps activity={activity} phrase="调用 read_file" />);
    });
    const rows = [...host.querySelectorAll(".chat-activity-steps li")].map((n) => n.textContent);
    // 短语本身那一行不重复列出来。
    expect(rows).toEqual(["调用 Shell"]);
    expect(host.querySelector(".execution-ribbon")).toBeNull();
  });

  it("renders nothing when there is no process text", async () => {
    await act(async () => {
      root!.render(
        <ChatActivitySteps
          activity={{ items: [item("runtime.system", { summary: "正在等待本轮模型结果" })] }}
          phrase="正在等待本轮模型结果"
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
