// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mock = vi.hoisted(() => ({ searchWeb: vi.fn(), resources: vi.fn(), addLink: vi.fn(),
  extractSkill: vi.fn(), saveSkill: vi.fn(), setSkillEnabled: vi.fn(), close: vi.fn(), changed: vi.fn() }));
vi.mock("../src/lib/studio-client", () => ({ projectClient: mock }));
vi.mock("../src/lib/task-model-catalog", () => ({ loadTaskModelRoutes: async () => [] }));
import { ProjectWebSearchDialog } from "../src/components/project-web-search-dialog";
import { ProjectSkillExtractDialog } from "../src/components/project-skill-extract-dialog";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const project = {projectId: "project-1"} as any;
const skill = {name: "report", description: "报告生成", instructions: "先检查数据，再生成报告。", files: []};
const click = async (text: string) => {
  const button = [...document.querySelectorAll("button")].find(item => item.textContent === text);
  expect(button, text).toBeTruthy();
  await act(async () => button!.click());
};
const input = async (label: string, value: string) => {
  const field = document.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event("input", {bubbles: true}));
  });
};
beforeEach(() => {
  vi.resetAllMocks();
  HTMLDialogElement.prototype.showModal = function() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function() { this.removeAttribute("open"); };
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  mock.resources.mockResolvedValue({links: []});
  mock.searchWeb.mockResolvedValue({results: [{url: "https://example.com/report", title: "报告", content: "说明"}]});
  mock.extractSkill.mockResolvedValue({status: "ready", reply: "已生成", followUpQuestions: [], skill});
  mock.saveSkill.mockResolvedValue({skillId: "skill-1", revision: 1, skill, enabled: false});
  mock.setSkillEnabled.mockResolvedValue({});
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });

it("searches independently, then adds only selected pages to this project", async () => {
  await act(async () => root.render(<ProjectWebSearchDialog project={project} onClose={mock.close} onChanged={mock.changed} />));
  expect(document.querySelector('dialog input[type="file"]')).toBeNull();
  await input("搜索网络资源或输入 URL", "公开报告"); await click("搜索");
  expect(mock.addLink).not.toHaveBeenCalled();
  expect(mock.searchWeb).toHaveBeenCalledWith("project-1", "公开报告", expect.any(AbortSignal));
  await act(async () => document.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
  await click("加入项目");
  expect(mock.addLink).toHaveBeenCalledWith("project-1", "https://example.com/report", "报告");
  expect(mock.changed).toHaveBeenCalled(); expect(mock.close).toHaveBeenCalled();
});

it("aborts a pending search when its dialog closes", async () => {
  mock.searchWeb.mockImplementation(() => new Promise(() => {}));
  await act(async () => root.render(<ProjectWebSearchDialog project={project} onClose={mock.close} onChanged={mock.changed} />));
  await input("搜索网络资源或输入 URL", "公开报告"); await click("搜索");
  const signal = mock.searchWeb.mock.calls[0][2] as AbortSignal;
  expect(signal.aborted).toBe(false);
  await act(async () => root.render(null));
  expect(signal.aborted).toBe(true); expect(mock.addLink).not.toHaveBeenCalled();
});

it("generates an editable preview and saves a project draft without enabling it", async () => {
  await act(async () => root.render(<ProjectSkillExtractDialog projectId="project-1" threadId="task-1" onClose={mock.close} />));
  await click("生成草稿");
  expect(mock.saveSkill).not.toHaveBeenCalled(); expect(mock.setSkillEnabled).not.toHaveBeenCalled();
  expect(document.querySelector<HTMLTextAreaElement>('[aria-label="项目技能步骤"]')?.value).toBe(skill.instructions);
  await input("项目技能名称", "月度报告"); await click("保存草稿");
  expect(mock.saveSkill).toHaveBeenCalledWith("project-1", {displayName: "月度报告", skill, sourceThreadId: "task-1"});
  expect(mock.setSkillEnabled).not.toHaveBeenCalled(); expect(mock.close).toHaveBeenCalled();
});

it("retries enabling a saved draft without creating a duplicate", async () => {
  mock.setSkillEnabled.mockRejectedValueOnce(new Error("请重试"));
  await act(async () => root.render(<ProjectSkillExtractDialog projectId="project-1" threadId="task-1" artifactId="file-1" onClose={mock.close} />));
  await click("生成草稿"); await click("保存并启用");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("请重试");
  expect(mock.close).not.toHaveBeenCalled();
  await click("保存并启用");
  expect(mock.saveSkill).toHaveBeenCalledTimes(1); expect(mock.setSkillEnabled).toHaveBeenCalledTimes(2);
  expect(mock.setSkillEnabled).toHaveBeenLastCalledWith("project-1", expect.objectContaining({skillId: "skill-1", revision: 1}), true);
  expect(mock.close).toHaveBeenCalled();
});
