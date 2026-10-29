// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const mock = vi.hoisted(() => ({ update: vi.fn(), create: vi.fn(), saved: vi.fn(), close: vi.fn() }));
vi.mock("../src/lib/studio-client", () => ({ projectClient: { update: mock.update, create: mock.create } }));
import { ProjectEditDialog } from "../src/components/project-create-dialog";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const project = {projectId:"p1", name:"原项目", instructions:"保留指令", skillReferences:["report@1"], mcpReferences:["database"]} as any;
beforeEach(() => {
  vi.resetAllMocks();
  HTMLDialogElement.prototype.showModal = function() { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function() { this.removeAttribute("open"); };
  host=document.createElement("div"); document.body.append(host); root=createRoot(host);
  mock.update.mockImplementation(async (_id, patch) => ({...project,...patch}));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
async function name(value: string) {
  await act(async () => {
    const input=document.querySelector<HTMLInputElement>('[aria-label="项目名称"]')!;
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(input,value);
    input.dispatchEvent(new Event("input",{bubbles:true}));
  });
}
async function save() { await act(async () => document.querySelector<HTMLButtonElement>('button[type="submit"]')!.click()); }
it("renames the same project and sends only its trimmed name",async () => {
  await act(async () => root.render(<ProjectEditDialog project={project} onSaved={mock.saved} onClose={mock.close} />));
  expect(document.querySelector('h2')?.textContent).toBe("编辑项目");
  expect(document.querySelector<HTMLInputElement>('[aria-label="项目名称"]')?.value).toBe("原项目");
  expect(document.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  await name("   "); expect(document.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  await name(" 新项目 "); await save();
  expect(mock.update).toHaveBeenCalledExactlyOnceWith("p1",{name:"新项目"});
  expect(mock.create).not.toHaveBeenCalled();
  expect(mock.saved).toHaveBeenCalledWith({...project,name:"新项目"});
  expect(mock.close).toHaveBeenCalledTimes(1);
});
it("retains an unsuccessful name so it can be corrected and retried",async () => {
  mock.update.mockRejectedValueOnce(new Error("项目名称已存在"));
  await act(async () => root.render(<ProjectEditDialog project={project} onSaved={mock.saved} onClose={mock.close} />));
  await name("已存在项目"); await save();
  expect(document.querySelector('[role="alert"]')?.textContent).toBe("项目名称已存在");
  expect(mock.close).not.toHaveBeenCalled(); expect(mock.saved).not.toHaveBeenCalled();
  await name("新名称"); await save();
  expect(mock.update).toHaveBeenLastCalledWith("p1",{name:"新名称"});
  expect(mock.close).toHaveBeenCalledTimes(1);
});
