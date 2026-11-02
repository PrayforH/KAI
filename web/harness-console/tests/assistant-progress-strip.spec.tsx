// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantProgressStrip } from "../src/components/assistant-progress-strip";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const notices = [
  { itemId: "n1", objectiveId: "o1", title: "行业快报", content: "已生成第一期", kind: "succeeded", createdAt: "2026-10-30T03:00:00Z", readAt: null, threadId: "assistant_o1" },
  { itemId: "n2", objectiveId: "o2", title: "预算受限职责", content: "预算不足，需要你处理", kind: "failed", createdAt: "2026-10-30T04:00:00Z", readAt: null, threadId: null },
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

it("collapses to a single floating line so it cannot push the composer out of view", async () => {
  await act(async () => root.render(<AssistantProgressStrip />));
  await act(async () => { await Promise.resolve(); });
  const strip = host.querySelector(".assistant-progress-strip");
  expect(strip).toBeTruthy();
  // 折叠态不渲染明细列表，只有一行摘要
  expect(host.querySelectorAll("li").length).toBe(0);
  expect(strip?.textContent).toContain("后台任务进展");
  expect(strip?.textContent).toContain("需要你处理");
});

it("expands on demand and links back to the original task", async () => {
  await act(async () => root.render(<AssistantProgressStrip />));
  await act(async () => { await Promise.resolve(); });
  const toggle = host.querySelector<HTMLButtonElement>(".assistant-progress-toggle");
  expect(toggle).toBeTruthy();
  await act(async () => toggle!.click());
  expect(host.querySelectorAll("li").length).toBe(2);
  const link = host.querySelector<HTMLAnchorElement>(".assistant-progress-link");
  expect(link?.getAttribute("href")).toBe("/?thread=assistant_o1");
});

it("can be dismissed for the session", async () => {
  await act(async () => root.render(<AssistantProgressStrip />));
  await act(async () => { await Promise.resolve(); });
  const hide = host.querySelector<HTMLButtonElement>(".assistant-progress-hide");
  await act(async () => hide!.click());
  expect(host.querySelector(".assistant-progress-strip")).toBeNull();
});

it("distinguishes a ready artifact from delivery and keeps its original run", async () => {
  fetcher.mockImplementation(async () => Response.json([{
    ...notices[0], kind: "artifact_ready", runId: "run-original", content: "成果可预览下载",
  }]));
  await act(async () => root.render(<AssistantProgressStrip />));
  await act(async () => host.querySelector<HTMLButtonElement>(".assistant-progress-toggle")!.click());
  expect(host.querySelector(".assistant-progress-kind")?.textContent).toBe("成果已就绪");
  expect(host.textContent).not.toContain("已交付");
  const link = host.querySelector<HTMLAnchorElement>(".assistant-progress-link")!;
  expect(new URL(link.href).searchParams.get("run")).toBe("run-original");
});
