// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { studioClient, type DeepagentsProjectSource } from "../src/lib/studio-client";
import { projectSourceChanges } from "../src/lib/project-source-changes";
import { AgentProjectCode } from "../src/components/agent-studio/agent-project-code";

vi.mock("../src/components/agent-studio/project-source-editor", () => ({ ProjectSourceEditor: ({ content, theme, wrap }: { content: string; theme: string; wrap: boolean }) => <pre data-theme={theme} data-wrap={wrap}>{content}</pre> }));
vi.mock("../src/components/agent-studio/project-source-diff", () => ({ ProjectSourceDiff: ({change}: {change: {before?: {content: string}; after?: {content: string}}}) => <div data-testid="code-diff"><del>{change.before?.content}</del><ins>{change.after?.content}</ins></div> }));
vi.mock("../src/components/agent-studio/project-file-tree", () => ({ ProjectFileTree: () => <div /> }));
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
});
