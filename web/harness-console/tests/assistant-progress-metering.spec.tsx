// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssistantProgressStrip } from "../src/components/assistant-progress-strip";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const notices = [
  {
    itemId: "n1",
    objectiveId: "o1",
    title: "行业快报",
    content: "已生成第一期",
    kind: "succeeded",
    createdAt: "2026-10-31T03:00:00Z",
    readAt: null,
    threadId: "assistant_o1",
    metrics: { tokens: 1234, costUsd: 0.02, unknownUsage: false },
    durationSeconds: 45,
  },
  {
    itemId: "n2",
    objectiveId: "o2",
    title: "天气提醒",
    content: "本次未能获取预报",
    kind: "succeeded",
    createdAt: "2026-10-31T04:00:00Z",
    readAt: null,
    threadId: null,
    metrics: { tokens: null, costUsd: null, unknownUsage: true },
    durationSeconds: 12,
  },
  {
    itemId: "n3",
    objectiveId: "o3",
    title: "旧进展",
    content: "历史载荷没有计量",
    kind: "succeeded",
    createdAt: "2026-10-31T05:00:00Z",
    readAt: null,
    threadId: null,
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

function metricLine(itemId: string): Element | null {
  const row = [...host.querySelectorAll(".assistant-progress-strip li")].find((node) =>
    (node.textContent ?? "").includes(itemId),
  );
  return row?.querySelector(".assistant-progress-metrics") ?? null;
}

describe("assistant progress strip metering", () => {
  it("shows tokens (thousand-separated), cost and duration for a measured run", async () => {
    await mountExpanded();
    const rows = [...host.querySelectorAll(".assistant-progress-strip li")];
    const measured = rows.find((node) => (node.textContent ?? "").includes("行业快报"));
    const metrics = measured?.querySelector(".assistant-progress-metrics");
    expect(metrics).not.toBeNull();
    expect(metrics?.getAttribute("data-metering")).toBe("measured");
    expect(metrics?.textContent).toContain("tokens 1,234");
    expect(metrics?.textContent).toContain("cost $0.02");
    expect(metrics?.textContent).toContain("耗时 45 秒");

    // 折叠态摘要不铺计量，保持一行浮标密度。
    const summary = host.querySelector(".assistant-progress-summary")?.textContent ?? "";
    expect(summary).not.toContain("tokens");
  });

  it("says tokens 未计量 instead of faking zero", async () => {
    await mountExpanded();
    const rows = [...host.querySelectorAll(".assistant-progress-strip li")];
    const unknown = rows.find((node) => (node.textContent ?? "").includes("天气提醒"));
    const metrics = unknown?.querySelector(".assistant-progress-metrics");
    expect(metrics?.getAttribute("data-metering")).toBe("unknown");
    expect(metrics?.textContent).toContain("tokens 未计量");
    expect(metrics?.textContent).toContain("耗时 12 秒");
    expect(metrics?.textContent).not.toContain("tokens 0");
    expect(unknown?.textContent ?? "").not.toMatch(/tokens\s*0\b/);
  });

  it("renders no metering line for legacy notices", async () => {
    await mountExpanded();
    expect(metricLine("旧进展")).toBeNull();
    // 三条 notice 里只有两条有计量行。
    expect(host.querySelectorAll(".assistant-progress-metrics")).toHaveLength(2);
  });
});
