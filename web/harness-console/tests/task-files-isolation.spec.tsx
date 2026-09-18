// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { WorkbenchRail } from "../src/components/workbench-rail";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

it("never renders the previous task's files while the new task request is pending", async () => {
  let resolveSecond!: (response: Response) => void;
  const fetcher = vi.fn()
    .mockResolvedValueOnce(Response.json([{ artifact_id: "a", thread_id: "task-a", name: "A-private.txt", media_type: "text/plain" }]))
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveSecond = resolve; }));
  vi.stubGlobal("fetch", fetcher);
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  const props = { open: true, onClose() {}, expanded: false, onToggleExpanded() {}, observabilityHref: null, runPhase: "completed" };
  try {
    await act(async () => root.render(<WorkbenchRail {...props} threadId="task-a" />));
    expect(container.textContent).toContain("A-private.txt");
    await act(async () => root.render(<WorkbenchRail {...props} threadId="task-b" />));
    expect(container.textContent).not.toContain("A-private.txt");
    expect(fetcher.mock.calls[1][0]).toContain("thread_id=task-b");
    await act(async () => resolveSecond(Response.json([
      { artifact_id: "b", thread_id: "task-b", name: "B-private.txt", media_type: "text/plain" },
      { artifact_id: "a", thread_id: "task-a", name: "A-private.txt", media_type: "text/plain" },
    ])));
    expect(container.textContent).toContain("B-private.txt");
    expect(container.textContent).not.toContain("A-private.txt");
    const downloadLinks = [...container.querySelectorAll('a.rail-download[href*="artifacts/"]')];
    const previewButtons = [...container.querySelectorAll("button.workbench-rail-file")];
    expect(downloadLinks).toHaveLength(1);
    expect(previewButtons).toHaveLength(1);
    expect(downloadLinks.every((link) => link.getAttribute("href")?.includes("thread_id=task-b"))).toBe(true);
    expect(previewButtons.map((button) => button.textContent ?? "")).toEqual([
      expect.stringContaining("B-private.txt"),
    ]);
  } finally { await act(async () => root.unmount()); container.remove(); vi.unstubAllGlobals(); }
});
