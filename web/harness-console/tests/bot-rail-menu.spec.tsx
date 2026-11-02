// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BotRailMenu } from "../src/components/bot-rail-menu";
import type { Specialist } from "../src/lib/assistant-client";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root, host: HTMLDivElement;
const update = vi.fn(), edit = vi.fn(), archive = vi.fn();
const bot = {specialistId: "qa-bot", name: "测试 Bot", config: {}} as Specialist;
beforeEach(async () => {
  vi.clearAllMocks(); update.mockResolvedValue(undefined); archive.mockResolvedValue(undefined);
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<BotRailMenu bot={bot} groups={["工作"]} onUpdate={update} onEdit={edit} onArchive={archive} />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
async function click(text: string) {
  const button = [...document.querySelectorAll("button")].find(node => node.textContent === text)!;
  expect(button).toBeTruthy(); await act(async () => button.click());
}
async function open() { await click("···"); }
it("saves navigation preferences without enabling or dispatching a Bot", async () => {
  await open(); await click("置顶"); expect(update).toHaveBeenLastCalledWith({pinned: true});
  await open(); await click("移动到分组 ›"); await click("工作"); expect(update).toHaveBeenLastCalledWith({group: "工作"});
  await open(); await click("标记为未读"); expect(update).toHaveBeenLastCalledWith({unread: true});
  await open(); await click("从侧边栏隐藏"); expect(update).toHaveBeenLastCalledWith({hidden: true});
  expect(archive).not.toHaveBeenCalled(); expect(edit).not.toHaveBeenCalled();
});
it("requires a concrete archive choice and retains the menu on failure", async () => {
  await open(); await click("归档 Bot…"); expect(document.querySelector('[role="alertdialog"]')).not.toBeNull();
  expect(archive).not.toHaveBeenCalled(); await click("取消"); expect(archive).not.toHaveBeenCalled();
  await open(); await click("归档 Bot…"); archive.mockRejectedValueOnce(new Error("保存冲突，请刷新")); await click("归档");
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("保存冲突");
});
it("opens editing without changing runtime state, and supports Escape", async () => {
  await open(); await click("编辑资料"); expect(edit).toHaveBeenCalledOnce(); expect(update).not.toHaveBeenCalled();
  await open(); await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", {key: "Escape"})));
  expect(document.querySelector('[role="menu"]')).toBeNull(); expect(document.activeElement).toBe(host.querySelector("button"));
});
