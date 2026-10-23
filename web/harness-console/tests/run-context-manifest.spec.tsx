// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RunContextManifest } from "../src/components/run-context-manifest";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});
const open = async () =>
  act(async () => {
    const details = host.querySelector("details")!;
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
  });
const provenance = {
  manifest: {
    agent: { name: "analyst", version: "2" },
    project: {
      name: "季度分析",
      updatedAt: "2026-10-18",
      documents: [{ id: "doc", name: "项目数据.csv", sha256: "b".repeat(64) }],
      memories: [],
    },
    skills: [
      {
        name: "writer",
        source: "project:writer@3",
        content_hash: "a".repeat(64),
      },
    ],
    knowledge: [],
  },
  skillResolutions: [{ name: "writer", resolution: "agent_precedence" }],
};

it("loads only on demand and shows the actual conflict decision with pinned provenance", async () => {
  const fetcher = vi.fn(async () => Response.json(provenance));
  vi.stubGlobal("fetch", fetcher);
  await act(async () => root.render(<RunContextManifest runId="run-a" />));
  expect(fetcher).not.toHaveBeenCalled();
  await open();
  expect(fetcher).toHaveBeenCalledWith(
    "/api/harness/runs/run-a/context",
    expect.objectContaining({ cache: "no-store" }),
  );
  expect(host.textContent).toContain("同名冲突，使用智能体自带版本");
  expect(host.textContent).toContain("writer@3");
  expect(host.textContent).toContain("项目数据.csv");
  expect(host.textContent).toContain("analyst · 2");
});

it("shows a retryable error and preserves a distinct legacy state", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 500 }))
    .mockResolvedValueOnce(
      Response.json({ manifest: null, skillResolutions: [] }),
    );
  vi.stubGlobal("fetch", fetcher);
  await act(async () => root.render(<RunContextManifest runId="run-a" />));
  await open();
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  await act(async () => host.querySelector("button")!.click());
  expect(host.textContent).toContain("这个历史任务没有保存资源清单");
  expect(host.querySelector('[role="alert"]')).toBeNull();
});
