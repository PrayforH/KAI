// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentaConfiguration } from "../src/components/agent-studio/agenta-configuration";
import { DEFAULT_STUDIO_DRAFT, type StudioDraft } from "../src/lib/agent-studio";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLDivElement;

const MCP_OPTIONS = [
  { id: "knowledge-search", category: "tool" as const, label: "知识检索", description: "检索知识库", tools: [], network: "internal" as const, sendsUserData: false },
];

const draft: StudioDraft = {
  ...DEFAULT_STUDIO_DRAFT,
  id: "draft-panel",
  revision: 3,
  runtime: "deepagents",
  builtinTools: ["Read", "Glob", "Grep", "Write", "Bash"],
  mcpServers: ["knowledge-search"],
  pythonTools: [{ name: "summarise", description: "汇总", inputSchema: {}, code: "def run(): pass" }],
};

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(overrides: Partial<Parameters<typeof AgentaConfiguration>[0]> = {}) {
  act(() =>
    root.render(
      <AgentaConfiguration
        draft={draft}
        dirty={false}
        saving={false}
        writable
        onEdit={() => {}}
        onSave={() => {}}
        onPublish={() => {}}
        onCode={() => {}}
        onBuildChat={() => {}}
        onAddMcp={() => {}}
        mcpOptions={MCP_OPTIONS}
        onToggleMcp={() => {}}
        {...overrides}
      />,
    ),
  );
}

it("summarises the tools by source instead of listing every tool", () => {
  render();
  const rows = [...host.querySelectorAll("button")].map((button) =>
    (button.textContent ?? "").replace(/\s+/g, " ").trim(),
  );
  // One row per source, each carrying its count; the individual tool names are
  // still discoverable through the row's title.
  expect(rows.filter((text) => text.startsWith("内置工具")).length).toBe(1);
  expect(rows.some((text) => text.includes("内置工具") && text.includes("5 项"))).toBe(true);
  // MCP has its own group below; the tools group must not repeat it as a source
  // row. (The group's own summary reads 已绑定, not 项.)
  expect(rows.some((text) => text.startsWith("MCP 服务") && text.includes("项"))).toBe(false);
  expect(rows.filter((text) => text.startsWith("内置工具") || text.startsWith("Python 算子"))).toHaveLength(2);
  expect(rows.some((text) => text.includes("Python 算子") && text.includes("1 项"))).toBe(true);
  expect(host.textContent).not.toContain("built-in");
  const title = [...host.querySelectorAll("button[title]")].map((node) => node.getAttribute("title"));
  expect(title.some((value) => value?.includes("Read"))).toBe(true);
});

it("shows the draft's runtime on the advanced row and the code entry in the header", () => {
  render();
  const buttons = [...host.querySelectorAll("button")].map((button) => ({
    text: (button.textContent ?? "").trim(),
    title: button.getAttribute("title"),
  }));
  const advanced = buttons.find((button) => button.text.startsWith("高级设置"));
    expect(advanced).toBeTruthy();
    // The row reports the draft's runtime rather than a static description.
    expect(advanced?.text).toContain("deepagents");
  expect(host.textContent).toContain("deepagents");
  expect(host.textContent).not.toContain("运行时、权限与沙箱");
  expect(buttons.some((button) => button.text === "代码" && button.title === "查看这份配置的代码视图")).toBe(true);
});

it("opens each resource directly with its own scope, without disclosure menus", () => {
  const onEdit = vi.fn();
  render({ onEdit });
  const click = (label: string) => {
    const row = [...host.querySelectorAll("button")].find(button => button.textContent?.startsWith(label));
    expect(row).toBeDefined();
    act(() => row!.click());
  };
  click("MCP 服务器");
  expect(onEdit).toHaveBeenLastCalledWith("capabilities", "mcp");
  click("内置工具");
  expect(onEdit).toHaveBeenLastCalledWith("capabilities", "builtin");
  click("Python 算子");
  expect(onEdit).toHaveBeenLastCalledWith("capabilities", "python");
  click("技能");
  expect(onEdit).toHaveBeenLastCalledWith("skills", undefined);
  expect(host.querySelector("details")).toBeNull();
  expect(host.textContent).toContain("1 个");
  expect(host.textContent).toContain("知识检索");
});

it("opens the knowledge section from the 文件与知识 row, with its reference count", () => {
  const edited: string[] = [];
  render({
    draft: { ...draft, knowledgeReferences: ["cases", "policy"] },
    onEdit: (section) => { edited.push(section); },
  });

  const row = [...host.querySelectorAll("button")].find((button) =>
    (button.textContent ?? "").includes("文件与知识"),
  );
  expect(row, "the 文件与知识 row must exist").toBeDefined();
  expect(row!.textContent).toContain("2 个");

  act(() => row!.click());
  // Knowledge binding has its own section now: it used to open the tools section,
  // where there was no way to tick a base.
  expect(edited).toEqual(["knowledge"]);
});
