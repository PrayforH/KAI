// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { WorkbenchRail } from "../src/components/workbench-rail";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const files = [
  { artifact_id: "a1", thread_id: "task-a", name: "report.md", media_type: "text/markdown", size_bytes: 1_200 },
  { artifact_id: "a2", thread_id: "task-a", name: "flow.html", media_type: "text/html", size_bytes: 2_400 },
];
let host: HTMLDivElement | undefined;
let root: ReturnType<typeof createRoot> | undefined;

afterEach(() => {
  if (root) act(() => root!.unmount());
  host?.remove();
  host = undefined;
  root = undefined;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function render(onClose = vi.fn(), onToggleExpanded = vi.fn(), observabilityHref: string | null = null) {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(files)));
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      <WorkbenchRail
        open
        onClose={onClose}
        expanded={false}
        onToggleExpanded={onToggleExpanded}
        observabilityHref={observabilityHref}
        runPhase="completed"
        threadId="task-a"
      />,
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  return onClose;
}

const button = (label: string) =>
  [...host!.querySelectorAll("button")].find((item) => item.getAttribute("aria-label") === label) ?? null;
const rowFor = (name: string) =>
  [...host!.querySelectorAll(".workbench-rail-file")].find((item) => item.textContent?.includes(name)) ?? null;
const previewing = () => host!.querySelector(".rail-preview") !== null;
const enabled = (element: Element | null) => element !== null && !(element as HTMLButtonElement).disabled;
async function click(element: Element | null) {
  expect(element).not.toBeNull();
  await act(async () => { (element as HTMLElement).click(); await new Promise((resolve) => setTimeout(resolve, 10)); });
}

it("walks back and forward through the files it opened", async () => {
  await render();

  // The list itself is the first stop, so there is nothing to go back to yet.
  expect(previewing()).toBe(false);
  expect(enabled(button("后退"))).toBe(false);
  expect(enabled(button("前进"))).toBe(false);

  await click(rowFor("report.md"));
  expect(previewing()).toBe(true);
  expect(host!.textContent).toContain("report.md");
  // The browser stays beside the preview, with the open file marked.
  expect(host!.querySelectorAll(".workbench-rail-file")).toHaveLength(2);
  expect(host!.querySelector('.workbench-rail-file[aria-pressed="true"]')?.textContent)
    .toContain("report.md");
  expect(enabled(button("后退"))).toBe(true);
  expect(enabled(button("前进"))).toBe(false);

  await click(button("后退"));
  expect(previewing()).toBe(false);
  expect(enabled(button("前进"))).toBe(true);
  expect(enabled(button("后退"))).toBe(false);

  await click(button("前进"));
  expect(previewing()).toBe(true);
  expect(host!.textContent).toContain("report.md");

  // Picking another file after going back replaces the forward history.
  await click(button("后退"));
  await click(rowFor("flow.html"));
  expect(host!.textContent).toContain("flow.html");
  expect(enabled(button("前进"))).toBe(false);
});

it("squeezes a real column, and covers the conversation when expanded", () => {
  const experience = readFileSync(
    join(process.cwd(), "src/app/conversation-experience.css"),
    "utf8",
  );
  // The drawer takes a third column, so the reading column keeps a share of it.
  expect(experience).toContain("grid-column: 3");
  expect(experience).toMatch(/--rail-panel-width:\s*clamp\([^;]*50vw\)/);
  // Expanded, the conversation column collapses to nothing and the drawer keeps the rest.
  expect(experience).toContain(".console-shell.is-rail-expanded .workspace-stage.tasks-open { grid-template-columns: var(--app-sidebar-expanded-width) 0 minmax(0, 1fr); }");
  expect(experience).toContain(".console-shell.is-rail-expanded .chat-stage { min-width: 0; overflow: hidden; }");
  // An open file shares the drawer with the browser once it is wide enough.
  expect(experience).toContain('@container rail (min-width: 460px)');
  expect(experience).toMatch(/\.rail-files-section\[data-previewing="true"\]/);
});

it("gives both columns the drawer height so a long list scrolls on its own", () => {
  const experience = readFileSync(
    join(process.cwd(), "src/app/conversation-experience.css"),
    "utf8",
  );
  // Without the clamp the tallest column stretches the drawer and no list scrolls.
  expect(experience).toMatch(/\.rail-files-section\[data-previewing="true"\] \{[^}]*flex: 1 1 auto;/s);
  expect(experience).toMatch(/\.rail-files-section\[data-previewing="true"\] \{[^}]*grid-template-rows: minmax\(0, 1fr\);/s);
  expect(experience).toMatch(/\.rail-file-column \{[^}]*overflow-y: auto;/s);
  expect(experience).toMatch(/\.rail-preview-column \{[^}]*overflow: hidden;/s);
});

it("asks the shell to give the drawer the whole conversation area", async () => {
  const onToggleExpanded = vi.fn();
  await render(vi.fn(), onToggleExpanded);

  await click(button("扩展占满对话区"));
  expect(onToggleExpanded).toHaveBeenCalledTimes(1);
});

it("hands the whole drawer to the open file when the browser is hidden", async () => {
  await render();
  await click(rowFor("report.md"));

  const section = () => host!.querySelector(".rail-files-section");
  expect(section()?.getAttribute("data-previewing")).toBe("true");
  expect(section()?.getAttribute("data-list-hidden")).toBeNull();

  await click(button("隐藏文件列表"));
  expect(section()?.getAttribute("data-list-hidden")).toBe("true");
  expect(button("显示文件列表")).not.toBeNull();

  // The browser is only hidden by CSS, so its rows are one click away.
  await click(button("显示文件列表"));
  expect(section()?.getAttribute("data-list-hidden")).toBeNull();
  expect(host!.querySelectorAll(".workbench-rail-file")).toHaveLength(2);
});

it("opens the search field and closes the drawer with the panel mark", async () => {
  const onClose = await render();

  expect(host!.querySelector(".rail-file-search")).toBeNull();
  await click(button("搜索任务文件"));
  const field = host!.querySelector<HTMLInputElement>(".rail-file-search");
  expect(field).not.toBeNull();

  // Escape clears the search and puts the field away again.
  await act(async () => {
    field!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  expect(host!.querySelector(".rail-file-search")).toBeNull();

  await click(button("收起任务上下文"));
  expect(onClose).toHaveBeenCalledTimes(1);

  // The view tabs are gone: the header carries the file and observability icons.
  expect(host!.querySelector(".rail-tabs")).toBeNull();
  expect(host!.querySelector(".workbench-rail-task")).toBeNull();
});

it("returns to the file list from the header icon", async () => {
  await render();
  await click(rowFor("report.md"));
  expect(host!.querySelector(".rail-preview")).not.toBeNull();
  expect(button("文件列表")?.getAttribute("aria-current")).toBeNull();

  await click(button("文件列表"));
  expect(host!.querySelector(".rail-preview")).toBeNull();
  expect(button("文件列表")?.getAttribute("aria-current")).toBe("page");
});

it("links the current run into Langfuse, and says so before a run exists", async () => {
  await render(vi.fn(), vi.fn(), "/api/harness/observability?run_id=run_1&trace_id=");
  const link = host!.querySelector('a[href^="/api/harness/observability"]');
  expect(link?.getAttribute("href")).toBe("/api/harness/observability?run_id=run_1&trace_id=");
  expect(link?.getAttribute("target")).toBe("_blank");
  expect(link?.getAttribute("aria-label")).toContain("Langfuse");

  // Without a run there is nothing to open, so the icon states that instead.
  await act(async () => root!.unmount());
  host!.remove();
  await render();
  expect(host!.querySelector('a[href^="/api/harness/observability"]')).toBeNull();
  expect(host!.querySelector('.rail-bar-button.is-disabled[aria-label*="Trace"]')).not.toBeNull();
});
