import type { StudioEvalCase } from "./agent-studio";

type ImportRow = Record<string, unknown>;

export type EvalDatasetImportResult = {
  cases: StudioEvalCase[];
  errors: string[];
};

const HEADER_ALIASES: Record<string, string[]> = {
  id: ["id", "caseid", "用例id", "场景id", "编号"],
  label: ["label", "name", "title", "名称", "场景名称", "用例名称"],
  tag: ["tag", "type", "category", "类型", "场景类型", "分类"],
  prompt: ["prompt", "input", "query", "提示词", "输入", "问题"],
  terminalStatuses: ["terminalstatuses", "status", "终态", "预期终态"],
  requiredTools: ["requiredtools", "必须工具", "必须调用"],
  forbiddenTools: ["forbiddentools", "禁止工具", "禁止调用"],
  outputContains: ["outputcontains", "输出包含", "关键词"],
  approvalRequired: ["approvalrequired", "需要审批", "必须审批"],
  maxDurationSeconds: ["maxdurationseconds", "timeout", "最大耗时", "超时秒数"],
};

function normalizedKey(value: string) {
  return value.trim().toLowerCase().replace(/[\s_\-./]+/g, "");
}

function valueFor(row: ImportRow, field: keyof typeof HEADER_ALIASES): unknown {
  const aliases = new Set(HEADER_ALIASES[field].map(normalizedKey));
  const entry = Object.entries(row).find(([key]) => aliases.has(normalizedKey(key)));
  return entry?.[1];
}

function listValue(value: unknown, fallback: string[] = []): string[] {
  if (Array.isArray(value)) return value.map(String).map((item) => item.trim()).filter(Boolean);
  if (value === undefined || value === null || value === "") return fallback;
  return String(value).split(/[|,;；、\n]+/).map((item) => item.trim()).filter(Boolean);
}

function booleanValue(value: unknown): boolean {
  if (typeof value === "boolean") return value;
  return ["1", "true", "yes", "y", "是", "需要"].includes(String(value ?? "").trim().toLowerCase());
}

function tagValue(value: unknown): StudioEvalCase["tag"] {
  const normalized = String(value ?? "happy").trim().toLowerCase();
  if (["ambiguous", "ambiguity", "歧义", "模糊"].includes(normalized)) return "ambiguous";
  if (["safety", "safe", "安全", "边界"].includes(normalized)) return "safety";
  return "happy";
}

function caseId(value: unknown, label: string, index: number, used: Set<string>): string {
  const base = String(value || label || `case-${index + 1}`)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "") || `case-${index + 1}`;
  let candidate = base;
  let suffix = 2;
  while (used.has(candidate)) candidate = `${base}-${suffix++}`;
  used.add(candidate);
  return candidate;
}

function normalizeRows(rows: ImportRow[]): EvalDatasetImportResult {
  const errors: string[] = [];
  const used = new Set<string>();
  const cases = rows.flatMap((row, index) => {
    const expect = row.expect && typeof row.expect === "object"
      ? row.expect as ImportRow
      : {};
    const label = String(valueFor(row, "label") ?? row.label ?? "").trim();
    const prompt = String(valueFor(row, "prompt") ?? row.prompt ?? "").trim();
    if (!label || !prompt) {
      errors.push(`第 ${index + 1} 行缺少${!label ? "名称" : "提示词"}`);
      return [];
    }
    return [{
      id: caseId(valueFor(row, "id") ?? row.id, label, index, used),
      label,
      tag: tagValue(valueFor(row, "tag") ?? row.tag ?? (Array.isArray(row.tags) ? row.tags[0] : undefined)),
      prompt,
      expect: {
        terminalStatuses: listValue(valueFor(row, "terminalStatuses") ?? expect.terminalStatuses, ["succeeded"]),
        requiredTools: listValue(valueFor(row, "requiredTools") ?? expect.requiredTools),
        forbiddenTools: listValue(valueFor(row, "forbiddenTools") ?? expect.forbiddenTools),
        outputContains: listValue(valueFor(row, "outputContains") ?? expect.outputContains),
        approvalRequired: booleanValue(valueFor(row, "approvalRequired") ?? expect.approvalRequired),
        maxDurationSeconds: Math.max(1, Number(valueFor(row, "maxDurationSeconds") ?? expect.maxDurationSeconds ?? 180) || 180),
      },
    } satisfies StudioEvalCase];
  });
  return { cases, errors };
}

function parseCsv(text: string): ImportRow[] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === '"' && quoted && text[index + 1] === '"') {
      cell += '"';
      index += 1;
    } else if (character === '"') {
      quoted = !quoted;
    } else if (character === "," && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      row.push(cell);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += character;
    }
  }
  row.push(cell);
  if (row.some((value) => value.trim())) rows.push(row);
  const [headers = [], ...values] = rows;
  return values.map((cells) => Object.fromEntries(headers.map((header, index) => [header, cells[index] ?? ""])));
}

async function parseExcel(file: File): Promise<ImportRow[]> {
  const { Workbook } = await import("exceljs");
  const workbook = new Workbook();
  await workbook.xlsx.load(new Uint8Array(await file.arrayBuffer()) as never);
  const worksheet = workbook.worksheets[0];
  if (!worksheet) return [];
  const headers: string[] = [];
  worksheet.getRow(1).eachCell({ includeEmpty: true }, (cell, column) => {
    headers[column - 1] = cell.text.trim();
  });
  const rows: ImportRow[] = [];
  worksheet.eachRow((excelRow, rowNumber) => {
    if (rowNumber === 1) return;
    const row: ImportRow = {};
    headers.forEach((header, index) => {
      if (header) row[header] = excelRow.getCell(index + 1).text;
    });
    if (Object.values(row).some((value) => String(value).trim())) rows.push(row);
  });
  return rows;
}

export async function parseEvalDatasetFile(file: File): Promise<EvalDatasetImportResult> {
  const extension = file.name.split(".").at(-1)?.toLowerCase();
  let rows: ImportRow[];
  if (extension === "json") {
    const parsed: unknown = JSON.parse(await file.text());
    const values = Array.isArray(parsed)
      ? parsed
      : parsed && typeof parsed === "object" && Array.isArray((parsed as { cases?: unknown[] }).cases)
        ? (parsed as { cases: unknown[] }).cases
        : [];
    rows = values.filter((item): item is ImportRow => Boolean(item) && typeof item === "object");
  } else if (extension === "csv") {
    rows = parseCsv(await file.text());
  } else if (extension === "xlsx") {
    rows = await parseExcel(file);
  } else {
    throw new Error("仅支持 JSON、CSV 或 .xlsx Excel 文件");
  }
  if (rows.length === 0) throw new Error("文件中没有可导入的评测用例");
  return normalizeRows(rows);
}
