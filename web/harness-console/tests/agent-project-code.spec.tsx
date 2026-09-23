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
vi.mock("../src/lib/studio-client", () => ({ studioClient: { getDeepagentsProjectSource: vi.fn(), getDeepagentsProjectFile: vi.fn(), refreshDeepagentsProject: vi.fn(), downloadDeepagentsProject: vi.fn() } }));
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
  it("keeps agent files in the right rail and follows the interface theme", async () => {
    const draft = { ...DEFAULT_STUDIO_DRAFT, id: "draft-files", revision: 3, systemPrompt: "meeting instructions" };
    await act(async () => root.render(<AgentWorkspaceFiles draft={draft} baseline={draft} turns={[]} onClose={() => {}} />));
    expect(host.querySelector(".workbench-rail")).not.toBeNull();
    expect(host.querySelector('[aria-label="文件目录"]')).toBeNull();
    await act(async () => host.querySelector<HTMLButtonElement>('.workbench-rail-file')!.click());
    expect(host.querySelector("pre")?.textContent).toBe("meeting instructions");
    expect(host.querySelector("pre")?.dataset.theme).toBe("dark");
    await act(async () => button("扩展占满对话区").click());
    expect(button("还原对话区").getAttribute("aria-pressed")).toBe("true");
    await act(async () => button("还原对话区").click());
    expect(host.querySelector("pre")?.textContent).toBe("meeting instructions");
  });
  it("collapses skill attachments, preserves preview and searches inside closed groups", async () => {
    const draft = {...DEFAULT_STUDIO_DRAFT, id:"draft-grouped", skills:[{name:"archify",description:"Diagrams",instructions:"Draw diagrams",files:[{path:"assets/template.html",content:"<html>report</html>"},{path:"scripts/render.mjs",content:"render()"}]}]};
    await act(async () => root.render(<AgentWorkspaceFiles draft={draft} baseline={draft} turns={[]} onClose={() => {}}/>));
    const group = host.querySelector<HTMLDetailsElement>(".rail-file-group")!;
    expect(group.open).toBe(false);
    expect(group.textContent).toContain("技能 · archify");
    expect(group.textContent).toContain("3 个文件");
    expect(host.querySelectorAll(".workbench-rail-file")).toHaveLength(1);
    await act(async () => {group.open=true;group.dispatchEvent(new Event("toggle"));});
    expect(host.querySelectorAll(".workbench-rail-file")).toHaveLength(4);
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".workbench-rail-file")].find(b=>b.textContent?.includes("template.html"))!.click());
    expect(host.querySelector("pre")?.textContent).toBe("<html>report</html>");
    await act(async () => button("文件列表").click());
    await act(async () => {group.open=false;group.dispatchEvent(new Event("toggle"));});
    await act(async () => button("搜索任务文件").click());
    const search = host.querySelector<HTMLInputElement>('input[type="search"]')!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(search,"render.mjs");
      search.dispatchEvent(new Event("input",{bubbles:true}));
    });
    expect(host.querySelectorAll(".workbench-rail-file")).toHaveLength(1);
    expect(host.querySelector(".workbench-rail-file")?.textContent).toContain("render.mjs");
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
      expect([...host.querySelectorAll("button")].some(button => button.textContent === "收起代码")).toBe(false);
      await act(async () => (directory.querySelector('[aria-label="收起代码视图"]') as HTMLButtonElement).click());
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
    await act(async () => button("全部代码").click());
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


it("opens file search within the directory toolbar and clears its filter on close", async () => {
  await render();
  expect(button("显示文件树").getAttribute("aria-pressed")).toBe("false");
  expect(host.querySelector('[aria-label="DeepAgents 文件树"]')).toBeNull();
  await act(async () => button("显示文件树").click());
  expect(host.querySelector('[aria-label="筛选文件"]')).toBeNull();
  expect(button("全部代码").textContent).toBe("");
  expect(button("本次改动").textContent).toBe("");
  await act(async () => button("搜索文件").click());
  const input = host.querySelector<HTMLInputElement>('[aria-label="筛选文件"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "search.py");
    input.dispatchEvent(new Event("input", {bubbles: true}));
  });
  expect(host.querySelector('[aria-label="DeepAgents 文件树"]')?.textContent).toBe("tools/search.py");
  await act(async () => button("搜索文件").click());
  expect(host.querySelector('[aria-label="筛选文件"]')).toBeNull();
  expect(host.querySelector('[aria-label="DeepAgents 文件树"]')?.textContent).toContain("README.md");
});

it("opens the packaged DeepAgents assembly by default while retaining legacy entry points", async () => {
  const path = "src/sapling_deep_agents/agents/agent.py";
  vi.mocked(studioClient.getDeepagentsProjectSource).mockResolvedValue({...fixture, files: [...fixture.files, {path, size: 10, content: "build_agent()", unavailable: null}]});
  await render();
  expect(host.querySelector("pre")?.textContent).toBe("build_agent()");
  expect(host.querySelector('[aria-label="DeepAgents 文件树"]')).toBeNull();
  await act(async () => button("显示文件树").click());
  expect(host.querySelector('[aria-label="DeepAgents 文件树"]')?.textContent).toContain("agent.py");
});

it("loads only metadata until a file is opened and ignores late responses from the previous selection", async () => {
  vi.mocked(studioClient.getDeepagentsProjectSource).mockResolvedValue({...fixture,files:fixture.files.map(file => ({...file,content:null,deferred:true}))});
  const directory=document.createElement('div');document.body.append(directory);
  let resolveFirst!: (file: typeof fixture.files[number]) => void;
  vi.mocked(studioClient.getDeepagentsProjectFile).mockImplementation(async (_draft,_rev,path) => path==='agent.py' ? new Promise(resolve => {resolveFirst=resolve;}) : fixture.files.find(file=>file.path===path)!);
  function Split() {const [expanded,setExpanded]=useState(false);return <AgentProjectCode draftId="draft-lazy" revision={4} name="test" dirty={false} directoryTarget={directory} expanded={expanded} onExpandedChange={setExpanded} onClose={()=>{}}/>;}
  try {
    await act(async()=>root.render(<Split/>));
    expect(studioClient.getDeepagentsProjectFile).not.toHaveBeenCalled();
    await act(async()=>[...directory.querySelectorAll('button')].find(b=>b.textContent==='agent.py')!.click());
    expect(host.textContent).toContain('正在读取文件');
    await act(async()=>[...directory.querySelectorAll('button')].find(b=>b.textContent==='tools/search.py')!.click());
    expect(host.querySelector('pre')?.textContent).toBe('search()');
    await act(async()=>resolveFirst(fixture.files[0]));
    expect(host.querySelector('pre')?.textContent).toBe('search()');
    expect(studioClient.getDeepagentsProjectFile).toHaveBeenCalledTimes(2);
  } finally {directory.remove();}
});
