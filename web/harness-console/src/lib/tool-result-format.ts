/** Decode protocol envelopes without rewriting the tool's meaning. */
export function decodeToolResult(value: unknown, depth = 0): unknown {
  if (depth >= 8) return value;
  if (typeof value === "string") {
    try { return decodeToolResult(JSON.parse(value.trim()), depth + 1); }
    catch { return value; }
  }
  if (Array.isArray(value)) {
    if (value.length === 1 && value[0]?.type === "text" && typeof value[0]?.text === "string") {
      return decodeToolResult(value[0].text, depth + 1);
    }
    return value;
  }
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;
  if (record.structuredContent && typeof record.structuredContent === "object") {
    return decodeToolResult(record.structuredContent, depth + 1);
  }
  if (Array.isArray(record.content) && record.content.length === 1) {
    const block = record.content[0];
    if (block && typeof block === "object" && block.type === "text" && typeof block.text === "string") {
      return decodeToolResult(block.text, depth + 1);
    }
  }
  return value;
}

export function formatToolResult(value: string) {
  const decoded = decodeToolResult(value);
  return typeof decoded === "string" ? decoded : JSON.stringify(decoded, null, 2);
}

export function queryResultTable(value: string) {
  const decoded = decodeToolResult(value);
  if (!decoded || typeof decoded !== "object" || Array.isArray(decoded)) return null;
  const result = decoded as Record<string, unknown>;
  const columns = result.columns;
  if (!Array.isArray(columns) || !columns.every(c => typeof c === "string") ||
      !Array.isArray(result.rows) || !result.rows.every(r => Array.isArray(r) && r.length === columns.length)) return null;
  return { columns: result.columns as string[], rows: result.rows as unknown[][],
    returnedRows: typeof result.returned_rows === "number" ? result.returned_rows : result.rows.length,
    truncated: result.truncated === true };
}
