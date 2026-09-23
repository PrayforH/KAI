import { Workbook } from "exceljs";
import { expect, it } from "vitest";
import { officeKindFor, readSpreadsheet, spreadsheetColumn, validateOfficeArchive } from "../src/lib/office-preview";

async function workbookBytes() {
  const workbook = new Workbook();
  const sheet = workbook.addWorksheet("汇总");
  sheet.addRow(["项目", "值"]);
  sheet.addRow(["报告", 4]);
  sheet.getCell("B3").value = { formula: "B2*2", result: 8 };
  const detail = workbook.addWorksheet("明细");
  for (let i = 0; i < 250; i++) detail.addRow([i, '<script>alert(1)</script>']);
  workbook.addWorksheet("隐藏", { state: "hidden" });
  const bytes = await workbook.xlsx.writeBuffer();
  return new Uint8Array(bytes).buffer;
}
it("recognizes modern and legacy Office extensions and exact MIME types", () => {
  expect(officeKindFor("application/octet-stream", "数据.XLSX")).toBe("xlsx");
  expect(officeKindFor("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "artifact")).toBe("docx");
  expect(officeKindFor("application/octet-stream", "演示.pptx")).toBe("pptx");
  expect(officeKindFor("application/msword", "report.doc")).toBe("legacy-office");
  expect(officeKindFor("text/plain", "report.txt")).toBeNull();
});
it("previews visible sheets, cached formula results, and bounded rows without evaluating cell content", async () => {
  const bytes = await workbookBytes(); validateOfficeArchive(bytes);
  const sheets = await readSpreadsheet(bytes);
  expect(sheets.map(sheet => sheet.name)).toEqual(["汇总", "明细"]);
  expect(sheets[0].rows[1]).toEqual(["报告", "4"]);
  expect(sheets[0].rows[2][1]).toBe("8");
  expect(sheets[1].rows).toHaveLength(200);
  expect(sheets[1].totalRows).toBe(250);
  expect(sheets[1].rows[0][1]).toBe('<script>alert(1)</script>');
  expect(spreadsheetColumn(26)).toBe("AA");
});
it("rejects invalid archives and oversized ZIP expansion before parsing", async () => {
  expect(() => validateOfficeArchive(new ArrayBuffer(10))).toThrow("有效的 Office");
  const bytes = await workbookBytes(); const view = new DataView(bytes);
  for (let offset = 0; offset < bytes.byteLength - 46; offset++) {
    if (view.getUint32(offset, true) === 0x02014b50) { view.setUint32(offset + 24, 200 * 1024 * 1024, true); break; }
  }
  expect(() => validateOfficeArchive(bytes)).toThrow("展开后较大");
});
