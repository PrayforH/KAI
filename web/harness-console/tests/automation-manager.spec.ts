import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { automationClient } from "../src/lib/studio-client";

const page = readFileSync(join(process.cwd(), "src/app/studio/automation/page.tsx"), "utf8");
const component = readFileSync(
  join(process.cwd(), "src/components/agent-studio/automation-manager.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/agent-studio/automation-manager.module.css"),
  "utf8",
);

describe("Automation manager", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renders both tabs, empty states, templates and the add-task modal", () => {
    expect(page).toContain("AutomationManager");
    expect(component).toContain("定时任务");
    expect(component).toContain("运行记录");
    expect(component).toContain("开启你的第一个自动化任务吧");
    expect(component).toContain("+ 添加自动化");
    expect(component).toContain("自动化任务模版");
    expect(component).toContain("暂无运行记录");
    expect(component).toContain("搜索自动化/记录");
    expect(component).toContain("添加自动化任务");
    expect(component).toContain("输入任务名称");
    expect(component).toContain("添加提示词");
    expect(component).toContain("选择工作空间");
    expect(component).toContain("允许完全访问");
    expect(component).toContain("长期有效");
    // The 12 reference templates are all present.
    for (const name of [
      "每日 AI 新闻推送",
      "每日 5 个英语单词",
      "每日儿童睡前故事",
      "每周工作周报",
      "经典电影推荐",
      "历史上的今天",
      "每日一个为什么",
      "父母联系提醒",
      "体检预约提醒",
      "面试准备提醒",
      "会议前准备",
      "可爱萌宠手机壁纸",
    ]) {
      expect(component).toContain(name);
    }
  });

  it("themes both color modes through semantic tokens only", () => {
    expect(styles).toContain("codex-theme-v1");
    expect(styles).toContain('html[data-color-mode="light"]');
    expect(styles).toContain("@media (max-width: 620px)");
  });

  it("opens the run's conversation and can still locate its task", () => {
    expect(component).toContain("openRecordConversation");
    expect(component).toContain("/?thread=");
    expect(component).toContain("打开这次执行所在的会话");
    expect(component).toContain("定位任务");
    expect(styles).toContain(".recordTaskAction");
  });

  it("jumps from a run record to its automation task", () => {
    expect(component).toContain("openTaskFromRecord");
    expect(component).toContain("recordTaskLink");
    expect(component).toContain("taskCardFocused");
    expect(component).toContain("scrollIntoView");
    // A deleted task must not pretend to navigate.
    expect(component).toContain("已删除，无法跳转");
    expect(styles).toContain(".recordTaskLink");
    expect(styles).toContain(".taskCardFocused");
  });

  it("names the module 自动化任务", () => {
    expect(page).toContain('title: "自动化任务"');
  });

  it("shows a next-run preview before saving", () => {
    expect(component).toContain("下次执行");
    expect(component).toContain("previewNextRun");
  });

  it("talks to the studio automation API", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(input), init });
      if (String(input).endsWith("/pause")) {
        return Response.json({ taskId: "t1", revision: 2, status: "paused" });
      }
      return Response.json([]);
    });
    await automationClient.list();
    await automationClient.records();
    await automationClient.pause("t1");
    expect(calls.map((call) => call.url)).toEqual([
      "/api/studio/automations",
      "/api/studio/automations/records",
      "/api/studio/automations/t1/pause",
    ]);
    expect(calls[2].init?.method).toBe("POST");
  });
});
