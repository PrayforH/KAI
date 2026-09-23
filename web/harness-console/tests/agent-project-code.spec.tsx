// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import React, { act, useEffect, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { studioClient, type DeepagentsProjectSource } from "../src/lib/studio-client";
import { projectSourceChanges } from "../src/lib/project-source-changes";
import { AgentProjectCode } from "../src/components/agent-studio/agent-project-code";
import { AgentWorkspaceFiles } from "../src/components/agent-studio/agent-workspace-files";
import { DEFAULT_STUDIO_DRAFT } from "../src/lib/agent-studio";

vi.mock("../src/components/agent-studio/project-source-editor", () => ({ ProjectSourceEditor: ({ content, theme, wrap }: { content: string; theme: string; wrap: boolean }) => <pre data-theme={theme} data-wrap={wrap}>{content}</pre> }));
vi.mock("../src/components/agent-studio/project-source-diff", () => ({ ProjectSourceDiff: ({change}: {change: {before?: {content: string}; after?: {content: string}}}) => <div data-testid="code-diff"><del>{change.before?.content}</del><ins>{change.after?.content}</ins></div> }));
vi.mock("../src/components/agent-studio/project-file-tree", () => ({ ProjectFileTree: ({ paths, selected, onSelect }: { paths: string[]; selected: string; onSelect: (path: string) => void }) => {
  const selectRef = useRef(onSelect); selectRef.current = onSelect;
  // Pierre reports programmatic selection too, including its initial selection.
  useEffect(() => { if (selected) selectRef.current(selected); }, [selected]);
  return <div>{paths.map(path => <button key={path} onClick={() => onSelect(path)}>{path}</button>)}</div>;
} }));
vi.mock("../src/lib/studio-client", () => ({ studioClient: { getDeepagentsProjectSource: vi.fn(), downloadDeepagentsProject: vi.fn() } }));
const fixture: DeepagentsProjectSource = {
  revision: 4, digest: "abc", filename: "agent.zip", framework_version: "0.7.13",
  files: [{ path: "agent.py", size: 10, content: "print('hello')", unavailable: null },
    { path: "tools/search.py", size: 5, content: "search()", unavailable: null },
    { path: "README.md", size: 6, content: "# Run", unavailable: null }],
};
let root: Root; let host: HTMLDivElement;
beforeEach(() => {
  document.documentElement.dataset.colorMode = "dark";
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value) });
  vi.resetAllMocks(); vi.mocked(studioClient.getDeepagentsProjectSource).mockResolvedValue(fixture);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
const button = (label: string) => Array.from(host.querySelectorAll("button")).find(item => item.getAttribute("aria-label") === label || item.textContent === label)!;
async function render(revision = 4) { await act(async () => root.render(<AgentProjectCode draftId="draft-a" revision={revision} name="research-agent" dirty={true} onClose={() => {}} />)); }
describe("DeepAgents source workspace", () => {
  it("uses the same directory and preview expansion for agent files", async () => {
    const directory = document.createElement("div"); document.body.append(directory);
    const draft = { ...DEFAULT_STUDIO_DRAFT, id: "draft-files", revision: 3, systemPrompt: "meeting instructions" };
    function FilesView() {
      const [expanded, setExpanded] = useState(false);
      return <AgentWorkspaceFiles draft={draft} baseline={draft} turns={[]} onClose={() => {}} directoryTarget={directory} expanded={expanded} onExpandedChange={setExpanded} />;
    }
    try {
      await act(async () => root.render(<FilesView />));
      expect(directory.querySelector('[aria-label="智能体文件树"]')).not.toBeNull();
      expect(host.querySelector("pre")).toBeNull();
      await act(async () => [...directory.querySelectorAll("button")].find(item => item.textContent === "AGENTS.md")!.click());
      expect(host.querySelector("pre")?.textContent).toBe("meeting instructions");
      expect(host.querySelector("pre")?.dataset.theme).toBe("dark");
      await act(async () => button("收起文件").click());
      expect(host.querySelector("pre")).toBeNull();
      await act(async () => (directory.querySelector('[aria-label="展开文件预览"]') as HTMLButtonElement).click());
      expect(host.querySelector("pre")?.textContent).toBe("meeting instructions");
    } finally { directory.remove(); }
  });
  it("navigates actual files, copies selected code and downloads the displayed revision", async () => {
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) };
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: clipboard });
    await render();
    expect(host.textContent).toContain("有未保存配置");
    expect(host.querySelector("pre")?.textContent).toBe("print('hello')");
    await act(async () => button("下一个文件").click());
    expect(host.querySelector("pre")?.textContent).toBe("search()");
    await act(async () => button("复制文件内容").click());
    expect(clipboard.writeText).toHaveBeenCalledWith("search()");
    await act(async () => button("下载项目").click());
    expect(studioClient.downloadDeepagentsProject).toHaveBeenCalledWith("draft-a", 4);
    expect(host.querySelector("textarea")).toBeNull();
  });
  it("keeps the directory in the middle and expands the selected file in the right pane", async () => {
    const directory = document.createElement("div"); document.body.append(directory);
    function SplitView() {
      const [expanded, setExpanded] = useState(false);
      return <AgentProjectCode draftId="draft-a" revision={4} name="research-agent" dirty={false} onClose={() => {}} directoryTarget={directory} expanded={expanded} onExpandedChange={setExpanded} />;
    }
    try {
      await act(async () => root.render(<SplitView />));
      expect(directory.querySelector('[aria-label="DeepAgents 文件树"]')).not.toBeNull();
      expect(host.querySelector("pre")).toBeNull();
      expect(directory.querySelector('[aria-label="展开代码视图"]')).not.toBeNull();
      const file = [...directory.querySelectorAll("button")].find(button => button.textContent === "tools/search.py")!;
      await act(async () => file.click());
      expect(host.querySelector("pre")?.textContent).toBe("search()");
      expect(host.querySelector('[aria-label="DeepAgents 文件树"]')).toBeNull();
      await act(async () => button("收起代码").click());
      expect(host.querySelector("pre")).toBeNull();
      await act(async () => (directory.querySelector('[aria-label="展开代码视图"]') as HTMLButtonElement).click());
      expect(host.querySelector("pre")?.textContent).toBe("search()");
      expect(studioClient.getDeepagentsProjectSource).toHaveBeenCalledTimes(1);
    } finally { directory.remove(); }
  });
  it("follows interface theme without a selector or a stored override", async () => {
    localStorage.setItem("studio-code-theme", "dark");
    document.documentElement.dataset.colorMode = "light";
    await render();
    expect(host.querySelector("select")).toBeNull();
    expect(host.querySelector("pre")?.dataset.theme).toBe("light");
    await act(async () => { document.documentElement.dataset.colorMode = "dark"; });
    expect(host.querySelector("pre")?.dataset.theme).toBe("dark");
    await act(async () => button("自动换行").click());
    expect(host.querySelector("pre")?.dataset.wrap).toBe("true");
  });
  it("shows a revision diff, disables pending downloads and returns to current files", async () => {
    const after = {...fixture, revision: 5, files: fixture.files.map(f => f.path === "agent.py" ? {...f, content: "print('updated')"} : f)};
    await act(async () => root.render(<AgentProjectCode draftId="draft-a" revision={4} name="research-agent" dirty={false} comparison={{before: fixture, after}} comparisonPending onClose={() => {}}/>));
    expect(host.textContent).toContain("r4 → r5 · 待应用 · 1 个文件变化");
    expect(host.querySelector("del")?.textContent).toBe("print('hello')");
    expect(host.querySelector("ins")?.textContent).toBe("print('updated')");
    expect(button("下载项目").disabled).toBe(true);
    expect(studioClient.getDeepagentsProjectSource).not.toHaveBeenCalled();
    await act(async () => button("全部文件").click());
    expect(host.querySelector("pre")?.textContent).toBe("print('hello')");
    expect(studioClient.getDeepagentsProjectSource).toHaveBeenCalled();
  });
  it("detects added, removed and equal-sized binary changes without guessing text", () => {
    const binary = {path:"image.png",size:2,content:null,unavailable:"binary",digest:"old"};
    const result = projectSourceChanges({before:{...fixture,files:[fixture.files[0], binary]},after:{...fixture,files:[fixture.files[1],{...binary,digest:"new"}]}});
    expect(result.map(({path,status})=>({path,status}))).toEqual([{path:"agent.py",status:"deleted"},{path:"image.png",status:"modified"},{path:"tools/search.py",status:"added"}]);
  });
  it("clears old source while loading a new revision and shows server errors", async () => {
    await render();
    vi.mocked(studioClient.getDeepagentsProjectSource).mockRejectedValue(new Error("草稿修订已变化"));
    await render(5);
    expect(host.querySelector("pre")).toBeNull();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("草稿修订已变化");
  });
  it("carries its palette on the editor, so the task rail renders source the same way", () => {
    const module = readFileSync(join(process.cwd(), "src/components/agent-studio/agent-project-code.module.css"), "utf8");
    const frame = /\.sourceFrame \{([^}]*)\}/.exec(module)?.[1] ?? "";

    // The rail has no .workspace ancestor, so the frame itself must define the tokens.
    expect(frame).toContain("--source-font-mono");
    expect(frame).toContain("--source-bg");
    expect(frame).toContain("background: var(--source-bg)");
    expect(module).toMatch(/\.sourceFrame\[data-theme="light"\] \{[^}]*--source-bg: #ffffff/);
  });
});
