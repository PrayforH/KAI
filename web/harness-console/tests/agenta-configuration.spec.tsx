// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentaConfiguration } from "../src/components/agent-studio/agenta-configuration";
import { DEFAULT_STUDIO_DRAFT, type StudioDraft } from "../src/lib/agent-studio";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLDivElement;

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
  expect(rows.some((text) => text.includes("MCP 服务") && text.includes("1 项"))).toBe(true);
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

it("lists the bound MCP servers in a group and links to their management page", () => {
  const onAddMcp = vi.fn();
  render({ onAddMcp });
  const summary = [...host.querySelectorAll("summary")].find((node) =>
    (node.textContent ?? "").includes("MCP 服务器"),
  );
  expect(summary?.textContent).toContain("1 个已绑定");
  // Expanding shows what this agent bound, which is the panel's job; binding
  // itself happens in the Tools section and management on its own page.
  expect(host.textContent).toContain("knowledge-search");
  const add = [...host.querySelectorAll("button")].find((button) =>
    (button.textContent ?? "").includes("添加 MCP 服务器"),
  );
  add?.click();
  expect(onAddMcp).toHaveBeenCalledTimes(1);
  const manage = host.querySelector('a[href="/studio/capabilities"]');
  expect(manage?.textContent).toContain("管理 MCP 服务器");
});
