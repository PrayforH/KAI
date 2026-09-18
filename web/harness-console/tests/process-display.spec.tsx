// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { readFileSync } from "node:fs";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { ProcessDisplaySettings } from "../src/components/process-display-settings";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";
import { setDetailedProcess } from "../src/lib/process-display-preference";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root, host: HTMLDivElement, style: HTMLStyleElement;
const item = (event_type: string, sequence: number, title: string, summary?: string) => ({
  id: `${event_type}-${sequence}`, event_type, sequence, title, summary,
  kind: "run" as const, status: "completed", timestamp: `2026-09-15T00:00:0${sequence}Z`, metadata: {},
});
const completed = runActivitySchema.parse({ run_id: "process-empty", status: "succeeded", started_at: "2026-09-15T00:00:00Z", items: [
  item("run.provisioning", 1, "正在准备运行环境"),
  item("message.start", 2, "正在生成本轮回复"),
  item("runtime.result", 3, "回复生成完成"),
  item("run.succeeded", 4, "运行完成"),
] });
beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  setDetailedProcess(false);
  document.body.className = "codex-theme-v1";
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }));
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  style = document.createElement("style");
  style.textContent = "body.codex-theme-v1 .execution-tree { display: block; }\n" + readFileSync("src/app/conversation-experience.css", "utf8");
  document.head.append(style);
});
afterEach(() => { act(() => root.unmount()); host.remove(); style.remove(); document.body.className = ""; vi.unstubAllGlobals(); });
function render(activity = completed, responseStarted = false) { act(() => root.render(<ActivitySummary activity={activity} responseStarted={responseStarted} />)); }
function expand() { const button = host.querySelector<HTMLButtonElement>(".execution-disclosure")!; if (button.getAttribute("aria-expanded") !== "true") act(() => button.click()); }

it("does not offer an empty disclosure or render infrastructure noise in either mode", () => {
  for (const detailed of [false, true]) {
    act(() => setDetailedProcess(detailed)); render();
    expect(host.querySelector("button.execution-disclosure")).toBeNull();
    expect(host.querySelector(".execution-chevron")).toBeNull();
    expect(getComputedStyle(host.querySelector<HTMLElement>(".execution-tree")!).display).toBe("none");
    expect(host.textContent).not.toContain("正在准备运行环境");
    expect(host.textContent).not.toContain("未提供可展示");
    expect(host.textContent).not.toContain("正在输出回复");
  }
});
const thought = runActivitySchema.parse({ ...completed, run_id: "thought-dsh", items: [
  { ...item("reasoning.delta", 1, "思考", "先核对输入。\n"), metadata: { item_id: "thought-1" } },
  { ...item("reasoning.delta", 2, "思考", "再确认结论。"), metadata: { item_id: "thought-1" } },
  item("run.succeeded", 3, "完成"),
] });
it("keeps thoughts collapsed regardless of the detail preference and allows explicit expansion", () => {
  act(() => root.render(<><ProcessDisplaySettings /><ActivitySummary activity={thought} /></>));
  expand();
  expect(host.querySelector(".execution-ribbon input")).toBeNull();
  const reasoning = host.querySelector<HTMLDetailsElement>(".execution-reasoning")!;
  expect(reasoning.open).toBe(false);
  expect(reasoning.querySelector("summary")?.textContent).toContain("先核对输入。");
  act(() => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
  expect(reasoning.open).toBe(false);
  act(() => { reasoning.open = true; reasoning.dispatchEvent(new Event("toggle")); });
  expect(reasoning.open).toBe(true);
  expect(reasoning.textContent).toContain("再确认结论。");
  expect(host.textContent).not.toContain("运行权限");
  act(() => root.render(null)); render(thought); expand();
  expect(host.querySelector<HTMLDetailsElement>(".execution-reasoning")!.open).toBe(false);
});
it("does not expose arbitrary thinking metadata as provider content", () => {
  const activity = runActivitySchema.parse({ ...completed, items: [
    { ...item("runtime.system", 1, "模型已就绪"), metadata: { thinking: "原始内部字段" } },
    item("run.succeeded", 2, "完成"),
  ] });
  setDetailedProcess(true); render(activity);
  expect(host.textContent).not.toContain("原始内部字段");
  expect(host.querySelector(".execution-reasoning")).toBeNull();
});
it("sweeps the last tool while the model continues and stops for response, approval or completion", () => {
  const running = runActivitySchema.parse({ ...completed, run_id: "tool-tail-dsh", status: "running", items: [
    item("run.running", 1, "开始"),
    { ...item("tool.request", 2, "读取"), kind: "tool", metadata: { tool_call_id: "t1", name: "Read", arguments: { file_path: "notes.txt" } } },
    { ...item("tool.result", 3, "已读取"), metadata: { tool_call_id: "t1" } },
  ] });
  render(running);
  expect(host.querySelectorAll(".execution-row-sweep")).toHaveLength(1);
  expect(host.querySelector(".execution-row-sweep")?.textContent).toContain("notes.txt");
  expect(host.textContent).not.toContain("正在输出回复");
  render(running, true);
  expect(host.querySelectorAll(".execution-row-sweep")).toHaveLength(0);
  render(runActivitySchema.parse({ ...running, items: [...running.items, { ...item("approval.requested", 4, "审批"), metadata: { approval_id: "a1" } }] }));
  expect(host.querySelectorAll(".execution-row-sweep")).toHaveLength(0);
  render({ ...thought, run_id: "completed-dsh" }); expand();
  expect(host.querySelectorAll(".execution-row-sweep")).toHaveLength(0);
});
it("does not force thought rows open when another tab changes detail settings", () => {
  render(thought); expand();
  act(() => {
    window.localStorage.setItem("agent-studio:detailed-process:v1", "detailed");
    window.dispatchEvent(new StorageEvent("storage", { key: "agent-studio:detailed-process:v1" }));
  });
  expect(host.querySelector<HTMLDetailsElement>(".execution-reasoning")!.open).toBe(false);
});

it("appends a single-line tail without replacing the row or changing manual expansion", () => {
  const running = { ...thought, run_id: "tail-row", status: "running", items: thought.items.slice(0, 2) };
  render(running);
  const reasoning = host.querySelector<HTMLDetailsElement>(".execution-reasoning")!;
  const preview = reasoning.querySelector<HTMLElement>(".execution-reasoning-preview")!;
  expect(reasoning.querySelector("summary")!.textContent).toBe("思考·先核对输入。 再确认结论。");
  Object.defineProperty(preview, "scrollWidth", { value: 1200 });
  const continued = { ...running, items: [...running.items,
    { ...item("reasoning.delta", 3, "思考", " 持续打印。"), metadata: { item_id: "thought-1" } }] };
  render(continued);
  expect(host.querySelector(".execution-reasoning")).toBe(reasoning);
  expect(preview.scrollLeft).toBe(1200);
  expect(reasoning.open).toBe(false);
  act(() => { reasoning.open = true; reasoning.dispatchEvent(new Event("toggle")); });
  render({ ...continued, items: [...continued.items,
    { ...item("reasoning.delta", 4, "思考", " 仍然展开。"), metadata: { item_id: "thought-1" } }] });
  expect(reasoning.open).toBe(true);
});
it("retains intermediate prose and tool nodes across the next action", () => {
  // A thinking block alone no longer ends the answer, so prose followed only by
  // thinking stays with the response; prose followed by a tool moves to the
  // process log, matching the server's final-answer projection.
  const base = [
    { ...item("tool.request", 1, "读取"), kind: "tool", metadata: { tool_call_id: "stable-tool", name: "Read", arguments: { file_path: "notes.txt" } } },
    { ...item("tool.result", 2, "已读取"), metadata: { tool_call_id: "stable-tool" } },
    item("message.delta", 3, "说明", "已找到资料，继续核验。"),
    { ...item("reasoning.delta", 4, "思考", "下一轮核验"), metadata: { item_id: "next-thought" } },
  ];
  const running = runActivitySchema.parse({ ...completed, run_id: "stable-process", status: "running", items: base });
  render(running);
  const tool = host.querySelector('.execution-action');
  // No tool follows the prose yet, so it is not logged as progress.
  expect(host.querySelector('[data-commentary-source="progress"]')).toBeNull();
  render(runActivitySchema.parse({ ...running, items: [...base,
    { ...item("reasoning.delta", 5, "思考", "补充内容"), metadata: { item_id: "next-thought" } },
    { ...item("tool.request", 6, "读取"), kind: "tool", metadata: { tool_call_id: "next-tool", name: "Read", arguments: { file_path: "next.txt" } } },
  ] }));
  // A tool now follows: the prose becomes progress commentary in the log.
  const progress = host.querySelector('[data-commentary-source="progress"]');
  expect(progress?.textContent).toContain("已找到资料，继续核验。");
  expect(host.querySelector('.execution-action')).toBe(tool);
});
