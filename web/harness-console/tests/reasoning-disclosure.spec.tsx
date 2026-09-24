// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema, type RunActivity } from "../src/lib/activity-schema";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const item = (id: string, sequence: number, summary: string, event_type = "reasoning.delta"): RunActivity["items"][number] => ({
  id, sequence, summary, event_type, kind: "analysis", status: "running", title: "思考",
  timestamp: "2026-09-24T00:00:00Z", metadata: {},
});
const activity = (items: ReturnType<typeof item>[]) => runActivitySchema.parse({
  run_id: "stable-reasoning", status: "running", started_at: "2026-09-24T00:00:00Z", items,
});

it("keeps streaming thought previews still and preserves the reader's position when expanded", async () => {
  const host = document.createElement("div"); document.body.appendChild(host);
  const root = createRoot(host);
  let text = "先检查资料。";
  try {
    await act(async () => root.render(<ActivitySummary activity={activity([item("thought", 1, text)])} />));
    const preview = host.querySelector(".execution-reasoning-preview") as HTMLElement;
    for (let index = 0; index < 20; index++) {
      text += "继续核对来源。".repeat(20);
      await act(async () => root.render(<ActivitySummary activity={activity([item("thought", 1, text)])} />));
      expect(preview.textContent).toBe("进行中");
      expect(preview.scrollLeft).toBe(0);
      expect(host.querySelector(".execution-reasoning-label")?.getAttribute("data-running")).toBe("true");
      expect(host.querySelector(".execution-reasoning-body")).toBeNull();
    }
    await act(async () => {
      const details = host.querySelector(".execution-reasoning") as HTMLDetailsElement;
      details.open = true;
      details.dispatchEvent(new Event("toggle"));
    });
    const body = host.querySelector(".execution-reasoning-body") as HTMLElement;
    body.scrollTop = 64;
    text += "这是最新内容。";
    await act(async () => root.render(<ActivitySummary activity={activity([item("thought", 1, text)])} />));
    expect(host.querySelector(".execution-reasoning-body")).toBe(body);
    expect(body.scrollTop).toBe(64);
    expect(body.textContent).toBe(text);
    expect(host.querySelector(".execution-reasoning-summary")?.classList.contains("execution-row-sweep")).toBe(false);
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it("separates unlabelled thinking across tool steps and stops the previous thought", async () => {
  const host = document.createElement("div"); document.body.appendChild(host);
  const root = createRoot(host);
  const before = item("before-tool", 1, "先读取资料。");
  const tool = { ...item("tool", 2, "", "tool.request"), kind: "tool" as const, metadata: { tool_call_id: "read", name: "Read" } };
  try {
    await act(async () => root.render(<ActivitySummary activity={activity([before, tool])} />));
    expect(host.querySelector(".execution-reasoning")?.getAttribute("data-active")).toBe("false");
    expect(host.querySelector(".execution-reasoning-preview")?.textContent).toBe("先读取资料。");
    const next = activity([before, tool, item("after-tool", 3, "再核验结果。")]);
    await act(async () => root.render(<ActivitySummary activity={next} />));
    const thoughts = host.querySelectorAll(".execution-reasoning");
    expect(thoughts).toHaveLength(2);
    expect(thoughts[0].getAttribute("data-active")).toBe("false");
    expect(thoughts[0].textContent).toContain("先读取资料。");
    expect(thoughts[0].textContent).not.toContain("再核验结果。");
    expect(thoughts[1].getAttribute("data-active")).toBe("true");
    await act(async () => root.render(<ActivitySummary activity={next} responseStarted />));
    expect(host.querySelector('.execution-reasoning[data-active="true"]')).toBeNull();
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it("keeps one disclosure through four streamed file requests and results", async () => {
  const host = document.createElement("div"); document.body.appendChild(host);
  const root = createRoot(host);
  const events: ReturnType<typeof item>[] = [];
  let row: HTMLDetailsElement | null = null;
  let summary: Element | null = null;
  try {
    for (let index = 1; index <= 4; index++) {
      for (const completed of [false, true]) {
        events.push({
          ...item(`${index}-${completed}`, events.length + 1, completed ? "已读取" : "", completed ? "tool.result" : "tool.request"),
          kind: "tool", status: completed ? "succeeded" : "running",
          metadata: { tool_call_id: `read-${index}`, name: "Read", arguments: { file_path: `inputs/${index}.pdf` }, result_preview: completed ? `文件 ${index} 内容` : undefined },
        });
        await act(async () => root.render(<ActivitySummary activity={activity(events)} />));
        const next = host.querySelector(".execution-action") as HTMLDetailsElement;
        if (!row) { row = next; summary = next.querySelector("summary"); }
        expect(next).toBe(row);
        expect(next.querySelector("summary")).toBe(summary);
        expect(next.querySelector("strong")?.getAttribute("data-running")).toBe(String(!completed));
        expect(host.querySelector(".execution-phase")?.getAttribute("data-running")).toBe("true");
        expect(next.querySelector(".execution-row-sweep")).toBeNull();
        if (index === 1 && completed) row.open = true;
        if (index > 1) expect(row.open).toBe(true);
      }
    }
    expect(row?.textContent).toContain("读取了 4 个文件");
    expect(host.querySelectorAll(".execution-action-detail")).toHaveLength(4);
    await act(async () => root.render(<ActivitySummary activity={{ ...activity(events), status: "succeeded", items: [...events, item("done", 9, "", "run.succeeded")] }} />));
    expect(host.querySelector(".execution-action")).toBe(row);
    expect(host.querySelector('[data-running="true"]')).toBeNull();
  } finally { await act(async () => root.unmount()); host.remove(); }
});

it("keeps the overall running indicator during answer output and stops it on exit", async () => {
  const host = document.createElement("div"); document.body.appendChild(host);
  const root = createRoot(host);
  const view = activity([item("thought", 1, "正在核对")]);
  try {
    await act(async () => root.render(<ActivitySummary activity={view} responseStarted />));
    const phase = host.querySelector(".execution-phase");
    expect(phase?.getAttribute("data-running")).toBe("true");
    expect(phase?.textContent).toBe("正在处理");
    expect(host.querySelector('.execution-reasoning-label[data-running="true"]')).toBeNull();
    for (const status of ["succeeded", "failed", "cancelled", "waiting_approval"] as const) {
      const event = status === "waiting_approval" ? { ...item("approval", 2, "", "approval.requested"), metadata: { approval_id: "approval" } } : item(status, 2, "", `run.${status}`);
      await act(async () => root.render(<ActivitySummary activity={{ ...view, status, items: [...view.items, event] }} responseStarted />));
      expect(host.querySelector(".execution-phase")).toBe(phase);
      expect(host.querySelector('[data-running="true"]')).toBeNull();
    }
  } finally { await act(async () => root.unmount()); host.remove(); }
});
