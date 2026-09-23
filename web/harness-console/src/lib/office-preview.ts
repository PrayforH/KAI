export type OfficeKind = "xlsx" | "docx" | "pptx" | "legacy-office";
export const OFFICE_PREVIEW_LIMIT = 50 * 1024 * 1024;
export const SHEET_ROW_LIMIT = 200;
export const SHEET_COLUMN_LIMIT = 60;

export function officeKindFor(mediaType: string, name: string): OfficeKind | null {
  const ext = name.toLowerCase().split(".").pop();
  const type = mediaType.toLowerCase().split(";")[0];
  if (ext === "xlsx" || type === "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") return "xlsx";
  if (ext === "docx" || type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document") return "docx";
  if (ext === "pptx" || type === "application/vnd.openxmlformats-officedocument.presentationml.presentation") return "pptx";
  if (["xls", "doc", "ppt"].includes(ext ?? "") || ["application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint"].includes(type)) return "legacy-office";
  return null;
}

/** Bound ZIP expansion before an Office parser allocates image/XML buffers. */
export function validateOfficeArchive(buffer: ArrayBuffer) {
  if (buffer.byteLength > OFFICE_PREVIEW_LIMIT) throw new Error("文件超过 50 MB，请下载后查看。");
  const view = new DataView(buffer);
  let end = -1;
  for (let i = buffer.byteLength - 22; i >= Math.max(0, buffer.byteLength - 65557); i--) {
    if (view.getUint32(i, true) === 0x06054b50 && i + 22 + view.getUint16(i + 20, true) === buffer.byteLength) { end = i; break; }
  }
  if (end < 0) throw new Error("文件不是有效的 Office 文档，或已加密。请下载后查看。");
  const count = view.getUint16(end + 10, true);
  let offset = view.getUint32(end + 16, true);
  let expanded = 0;
  if (count > 10000) throw new Error("文件内容较多，请下载后查看。");
  for (let i = 0; i < count; i++) {
    if (offset + 46 > end || view.getUint32(offset, true) !== 0x02014b50) throw new Error("Office 文件目录损坏，无法预览。");
    expanded += view.getUint32(offset + 24, true);
    if (expanded > 150 * 1024 * 1024) throw new Error("文件展开后较大，请下载后查看。");
    offset += 46 + view.getUint16(offset + 28, true) + view.getUint16(offset + 30, true) + view.getUint16(offset + 32, true);
  }
}

export interface SheetPreview {
  name: string;
  rows: string[][];
  totalRows: number;
  totalColumns: number;
}

export async function readSpreadsheet(buffer: ArrayBuffer): Promise<SheetPreview[]> {
  const { Workbook } = await import("exceljs");
  const workbook = new Workbook();
  await workbook.xlsx.load(buffer);
  return workbook.worksheets.filter(sheet => sheet.state === "visible").map(sheet => ({
    name: sheet.name,
    totalRows: sheet.rowCount,
    totalColumns: sheet.columnCount,
    rows: Array.from({ length: Math.min(sheet.rowCount, SHEET_ROW_LIMIT) }, (_, row) =>
      Array.from({ length: Math.min(sheet.columnCount, SHEET_COLUMN_LIMIT) }, (_, column) => {
        const cell = sheet.getCell(row + 1, column + 1);
        if (cell.value instanceof Date) return cell.value.toLocaleDateString("zh-CN");
        if (cell.formula && cell.result === undefined) return `=${cell.formula}`;
        return cell.text;
      }),
    ),
  }));
}

export function spreadsheetColumn(index: number): string {
  let label = "";
  for (let value = index + 1; value > 0; value = Math.floor((value - 1) / 26)) label = String.fromCharCode(65 + (value - 1) % 26) + label;
  return label;
}
