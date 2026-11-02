// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantManager } from "../src/components/agent-studio/assistant-manager";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const objective = { objectiveId: "o1", title: "采购整改跟进", instructions: "核对补充材料", agentName: "audit", agentVersion: "1", projectId: null, status: "active", revision: 7, schedule: null, nextCheckAt: null, completionCriteria: "证据齐全", endsAt: null, notificationMode: "none" };

let root: Root;
let host: HTMLDivElement;
const fetcher = vi.fn();

function panel(status: string, checkpoint?: { id: string; summary: string }) {
  return {
    objectiveId: "o1",
    revision: 7,
    budget: null,
    managed: [{
      threadId: "thread-1",
      role: "execute",
      projectId: null,
      dependsOnObjectiveId: null,
      progress: {
        thread_id: "thread-1",
        title: "采购整改任务",
        status,
        summary: "整理整改证据",
        active: false,
        run_id: "run-1",
        duration_seconds: 12,
        checkpoint_id: checkpoint?.id ?? null,
        checkpoint_summary: checkpoint?.summary ?? null,
        metrics: null,
      },
    }],
    overview: [],
    aggregate: "",
  };
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  fetcher.mockReset();
  vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function renderManaged(nextPanel: ReturnType<typeof panel>) {
  fetcher.mockImplementation(async (url: string) => {
    if (url.endsWith("/objectives")) return Response.json([objective]);
    if (url.endsWith("/managed")) return Response.json(nextPanel);
    return Response.json([]);
  });
  await act(async () => root.render(<AssistantManager />));
  await flush();
  const tab = [...host.querySelectorAll("button")].find((button) => button.textContent === "项目托管");
  expect(tab).toBeTruthy();
  await act(async () => tab!.click());
  const item = [...host.querySelectorAll("button")].find((button) => button.textContent?.includes("采购整改跟进"));
  expect(item).toBeTruthy();
  await act(async () => item!.click());
  await flush();
}

it("opens the failed run in its original thread with checkpoint context", async () => {
  await renderManaged(panel("failed", { id: "checkpoint_e1", summary: "沙箱启动失败，未执行任何步骤" }));

  const replay = [...host.querySelectorAll("a")].find((link) => link.textContent === "打开失败回放 →");
  expect(replay).toBeTruthy();
  const target = new URL(replay!.getAttribute("href")!, "https://example.test");
  expect(target.pathname).toBe("/");
  expect(target.searchParams.get("thread")).toBe("thread-1");
  expect(target.searchParams.get("run")).toBe("run-1");
  expect(target.searchParams.get("checkpoint")).toBe("checkpoint_e1");
  expect(target.searchParams.get("trace")).toBe("1");
  // 错误态用中文，且把检查点摘要带到入口上，不要求用户先猜。
  expect(host.textContent).toContain("运行失败");
  expect(host.textContent).toContain("沙箱启动失败，未执行任何步骤");
});

it("does not render a replay entry when the latest run did not fail", async () => {
  await renderManaged(panel("succeeded", { id: "checkpoint_e1", summary: "证据已归档" }));

  expect([...host.querySelectorAll("a")].some((link) => link.textContent === "打开失败回放 →")).toBe(false);
  const openTask = [...host.querySelectorAll("a")].find((link) => link.textContent === "打开原任务 →");
  expect(openTask?.getAttribute("href")).toBe("/?thread=thread-1");
});

it("lands the deep link in the trace view focused on the failed run", () => {
  const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
  const trace = readFileSync(
    join(process.cwd(), "src/components/run-trace-console.tsx"),
    "utf8",
  );
  expect(page).toContain('params.get("trace") === "1"');
  expect(page).toContain("focusRunId={focusRunId}");
  expect(trace).toContain("focusRunId");
  expect(trace).toContain("setSelectedId((failed ?? runNodes[0]).id)");
});


it("toggles a read-only event subscription with the objective revision and no execution", async () => {
  const data = panel("succeeded");
  Object.assign(data.managed[0], { managedId: "watch-1", notifyOnProgress: true });
  await renderManaged(data);
  const off = [...host.querySelectorAll("button")].find(button => button.textContent === "关闭进展通知");
  expect(off).toBeTruthy();
  const previousFetch = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "PATCH") return Response.json({});
    return previousFetch(url, init);
  });
  await act(async () => off!.click());
  await flush();
  const writes = fetcher.mock.calls.filter(([, init]) => init?.method === "PATCH");
  expect(writes).toHaveLength(1);
  expect(writes[0][0]).toBe("/api/studio/assistant/objectives/o1/managed/watch-1/notifications");
  expect(JSON.parse(writes[0][1].body)).toEqual({ enabled: false, expectedRevision: 7 });
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/executions") &&
    fetcher.mock.calls.some(([other, init]) => other === url && init?.method === "POST"))).toBe(false);
});
