import { describe, expect, it } from "vitest";
import { Workbook } from "exceljs";
import { parseEvalDatasetFile } from "../src/lib/eval-dataset-import";

describe("evaluation dataset import", () => {
  it("maps JSON cases and preserves nested expectations", async () => {
    const file = new File([JSON.stringify({ cases: [{
      id: "risk-1",
      label: "风险查询",
      tag: "安全",
      prompt: "查询高风险事件",
      expect: {
        terminalStatuses: ["succeeded"],
        requiredTools: ["search_risk_events"],
        maxDurationSeconds: 90,
      },
    }] })], "eval.json", { type: "application/json" });

    const result = await parseEvalDatasetFile(file);
    expect(result.errors).toEqual([]);
    expect(result.cases[0]).toMatchObject({
      id: "risk-1",
      tag: "safety",
      prompt: "查询高风险事件",
      expect: { requiredTools: ["search_risk_events"], maxDurationSeconds: 90 },
    });
  });

  it("supports Chinese CSV headers and reports invalid rows", async () => {
    const file = new File([
      "编号,场景名称,场景类型,提示词,必须调用,超时秒数\n",
      "case-a,正常查询,正常,查询企业,search_risk_subjects,120\n",
      "case-b,缺少输入,歧义,,search_risk_events,60\n",
    ], "eval.csv", { type: "text/csv" });

    const result = await parseEvalDatasetFile(file);
    expect(result.cases).toHaveLength(1);
    expect(result.cases[0].expect.requiredTools).toEqual(["search_risk_subjects"]);
    expect(result.errors[0]).toContain("第 2 行缺少提示词");
  });

  it("imports the first worksheet from an xlsx file", async () => {
    const workbook = new Workbook();
    const sheet = workbook.addWorksheet("评测集");
    sheet.addRow(["id", "label", "tag", "prompt", "outputContains"]);
    sheet.addRow(["excel-1", "Excel 场景", "ambiguous", "分析事件", "来源|不确定性"]);
    const buffer = await workbook.xlsx.writeBuffer();
    const file = new File([buffer], "eval.xlsx", {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    });

    const result = await parseEvalDatasetFile(file);
    expect(result.cases[0]).toMatchObject({
      id: "excel-1",
      tag: "ambiguous",
      expect: { outputContains: ["来源", "不确定性"] },
    });
  });
});
