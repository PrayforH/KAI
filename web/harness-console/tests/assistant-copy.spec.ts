import {describe, expect, it} from "vitest";
import {assistantCopy, assistantPresentation, compactAssistantProgress, compactAssistantReceipt, assistantQuoteCopy} from "../src/lib/assistant-copy";
const id = "objective_35ee45a4baa148159c64d47c3713efb4";
describe("main assistant conversational copy", () => {
  it("removes coordination metadata but preserves the actual result", () => {
    expect(assistantCopy(`关注已建立，使用 follow_only 模式。\n\n- 关注关系为 ${id}，revision=2。\n\n结果通知已关闭，没有停止原任务。`)).toBe("关注已建立，使用 只跟进 模式。\n\n结果通知已关闭，没有停止原任务。");
  });
  it("keeps the blocking problem and required input", () => {
    const copy=assistantCopy(`任务 ${id} 未启动，revision=3，缺少仓库地址，请补充。`);
    expect(copy).toContain("未启动");expect(copy).toContain("缺少仓库地址");
    expect(copy).not.toContain(id);expect(copy).not.toContain("revision");
  });
  it("preserves business numbers and working destinations", () => {
    const text=`项目 #34840 预计 16h，部署 79。\n[查看文件](/?thread=${id})`;
    expect(assistantCopy(text)).toBe(text);
    expect(assistantCopy("已安排（specialist_bd6ae2c429d24bd88b771fd112219c62，revision=2）。")).toBe("已安排。");
  });
  it("translates actual settings and waiting states", () => {
    expect(assistantCopy("notify_on_progress=false；notification_error 为空。")).toContain("结果通知已关闭");
    expect(assistantCopy("[needs_input]，尚未启动。")).toBe("等你补充信息，尚未启动。");
  });
  it("compacts background narration while keeping a later blocking problem", () => {
    const copy=compactAssistantProgress("检查已结束。\n\n"+"一般运行细节。".repeat(90)+"\n\n下载失败，文件仍保留，需要重新下载。");
    expect(copy.length).toBeLessThan(500);expect(copy).toContain("下载失败");expect(copy).not.toContain("一般运行细节");
  });
  it("hides metadata-only before/after cards and retains forms and business results", () => {
    const value={kind:"summary",fallback_text:"通知开关已关闭",title:"关闭前后",data:{items:[{label:"关闭前",value:`revision=1，${id}`},{label:"关闭后",value:"notify_on_progress=false，revision=2"}]}};
    expect(assistantPresentation(value)).toBeNull();
    expect(assistantPresentation({...value,kind:"form"})).toEqual({...value,kind:"form"});
    expect(assistantPresentation({...value,data:{items:[{label:"预算",value:"300000000"}]}})?.data.items).toEqual([{label:"预算",value:"300000000"}]);
    expect(value.data.items[0].value).toContain(id);
  });
});

it("cleans labels of multiple links without changing their destinations", () => {
 const text=`[${id}](/?thread=${id}) [${id}](/?thread=${id})`;
 const copy=assistantCopy(text);
 expect(copy.match(new RegExp(id,"g"))).toHaveLength(2);
 expect(copy).toBe(`[](/?thread=${id}) [](/?thread=${id})`);
});

it("reduces coordination narration while keeping later blockers", () => {
 const text="结果通知已关闭。没有停止原任务。\n\n这是过去的运行记录，不是新结果。\n\n文件下载失败，请重新下载。";
 const copy=compactAssistantReceipt(text);
 expect(copy).toContain("结果通知已关闭");expect(copy).not.toContain("过去的运行记录");
 expect(copy).toContain("文件下载失败");expect(copy).not.toContain("succeeded");
 expect(assistantCopy("状态 succeeded，schedule_minutes 待补充。")).toBe("状态 本次处理已结束，执行频率 待补充。");
});

it("removes a complete or truncated legacy source footer from quoted receipts", () => {
 for(const url of ["/assistant?bot=specialist_bd6ae2c429d24bd88b771fd112219c62)","/assistant?bot=specialist_bd6ae2c429d24bd88b771fd1"])
  expect(assistantQuoteCopy("报告已准备好。 [回到原对话]("+url)).toBe("报告已准备好。");
 expect(assistantQuoteCopy("[报告文件](/api/harness/artifacts/artifact_12345678)")).toBe("报告文件");
});
