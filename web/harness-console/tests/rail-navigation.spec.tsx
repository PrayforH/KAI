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

async function render(onClose = vi.fn(), onToggleExpanded = vi.fn()) {
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
  // Header, composer and turn index all live in one column, so expanding hides
  // the whole shell: the composer is fixed, so visibility carries the hiding.
  expect(experience).toContain(".console-shell.is-rail-expanded .task-content-shell { min-width: 0; overflow: hidden; visibility: hidden; }");
  expect(experience).not.toContain("visibility: visible");
  // An open file shares the drawer with the browser once it is wide enough.
  expect(experience).toContain('@container rail (min-width: 460px)');
  expect(experience).toMatch(/\.rail-files-section\[data-previewing="true"\]/);
});

it("themes the drawer's controls instead of hard-coding a dark press state", () => {
  const experience = readFileSync(
    join(process.cwd(), "src/app/conversation-experience.css"),
    "utf8",
  );
  // A hard-coded dark fill turned every pressed drawer button black in light mode.
  const literals = experience
    .split("\n")
    .filter((line) => line.startsWith(".rail-bar-button") || line.startsWith(".rail-bar-views"))
    .filter((line) => /background: #(2|1)/.test(line));

  expect(literals).toEqual([]);
  expect(experience).toMatch(/\.rail-bar-button\[aria-pressed="true"\] \{[^}]*background: var\(--codex-surface-active/);
  expect(experience).toMatch(/\.rail-bar-button:hover:not\(:disabled\) \{[^}]*background: var\(--codex-surface-hover/);
  expect(experience).toMatch(/\.rail-file-search \{[^}]*background: var\(--codex-surface/);
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

it("keeps the search field the width of the file list", async () => {
  await render();
  await click(button("搜索任务文件"));
  await click(rowFor("report.md"));

  const field = host!.querySelector(".rail-file-search");
  expect(field).not.toBeNull();
  // It sits in the browser column, so it is as wide as the rows below it.
  expect(field!.closest(".rail-file-column")).not.toBeNull();
  expect(host!.querySelector(".rail-search-row")).toBeNull();
});

it("tells the drawer's view icon apart from the list toggle", async () => {
  await render();
  const viewIcon = button("文件列表")?.querySelector("svg")?.innerHTML;
  const listIcon = button("隐藏文件列表")?.querySelector("svg")?.innerHTML;

  expect(viewIcon).toBeTruthy();
  expect(listIcon).toBeTruthy();
  expect(viewIcon).not.toBe(listIcon);
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

  // The drawer header now only controls files and their preview.
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

it("keeps the file drawer focused on file controls", async () => {
  await render();
  expect(host!.querySelector('a[href^="/api/harness/observability"]')).toBeNull();
  expect(host!.querySelector('[aria-label*="Trace"]')).toBeNull();
});

it("uses supplied agent files and their change previews without fetching another task", async () => {
  const fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock);
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
  const refresh = vi.fn();
  await act(async () => root!.render(<WorkbenchRail open onClose={vi.fn()} expanded={false} onToggleExpanded={vi.fn()} threadId="draft-a" runPhase={null}
    workspace={{files:[{artifact_id:"source:agent.py",name:"agent.py",media_type:"text/plain",change:"已修改"}],loading:false,error:"",onRefresh:refresh,renderPreview: file => <pre>{file.name} 的差异</pre>}} />));
  expect(fetchMock).not.toHaveBeenCalled();
  const group = host.querySelector("details")!;
  await act(async () => { group.open = true; group.dispatchEvent(new Event("toggle")); });
  expect(host.querySelector('[data-change="已修改"]')).not.toBeNull();
  await click(rowFor("agent.py"));
  expect(host.textContent).toContain("agent.py 的差异");
  expect(button("版本历史")).toBeNull();
  await click(button("刷新文件"));expect(refresh).toHaveBeenCalledOnce();
});

it("renders the deliverable first and mounts PDF conversion pages only after opening their folder", async () => {
  vi.stubGlobal("fetch", vi.fn());
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
  const files = [
    { artifact_id: 'final', name: 'report.md', media_type: 'text/markdown' },
    ...Array.from({ length: 70 }, (_, i) => ({ artifact_id: `page-${i}`, name: `images_cv3/p${i}.png`, media_type: 'image/png' })),
  ];
  await act(async () => root!.render(<WorkbenchRail open onClose={vi.fn()} expanded={false} onToggleExpanded={vi.fn()} threadId="pdf-task" runPhase={null} workspace={{ files, loading: false, error: '', renderPreview: file => <pre>{file.name}</pre> }} />));
  expect(host.querySelectorAll('.rail-file-row')).toHaveLength(1);
  const middle = [...host.querySelectorAll('details')].find(item => item.textContent?.includes('中间文件'))!;
  await act(async () => { middle.open = true; middle.dispatchEvent(new Event('toggle')); });
  expect(host.querySelectorAll('.rail-file-row')).toHaveLength(1);
  const folder = middle.querySelector('details')!;
  await act(async () => { folder.open = true; folder.dispatchEvent(new Event('toggle')); });
  expect(host.querySelectorAll('.rail-file-row')).toHaveLength(71);
});
