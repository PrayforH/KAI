import { isSensitiveLabel, isSecretFieldLabel } from "./conversation-secrets";
/** A display protocol only: submitted answers are ordinary user messages, never tool calls. */
export type ConversationQuestion = { id: string; label: string; type: "single" | "multi" | "text" | "password"; options: string[]; sensitive?: boolean };
export type ConversationInput = { version: 1; title: string; questions: ConversationQuestion[] };
function shortText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maximum;
}
export function isSensitiveQuestion(question: ConversationQuestion) {
  return question.type === "password" || question.sensitive === true || (question.type === "text" && isSensitiveLabel(question.label + " " + question.id));
}
type InputBlock = { start: number; end: number; input: ConversationInput };
function validateInput(raw: string, maximumOptions = 8): ConversationInput | null {
  if (raw.length > 16_000) return null;
  try {
    const data = JSON.parse(raw);
    if (!data || data.version !== 1 || !shortText(data.title, 160) || !Array.isArray(data.questions) || !data.questions.length || data.questions.length > 4) return null;
    if (Object.keys(data).some(key => !["version", "title", "questions"].includes(key))) return null;
    const ids = new Set<string>();
    const questions: ConversationQuestion[] = [];
    for (const item of data.questions) {
      if (!item || !shortText(item.id, 64) || !/^[\w-]+$/.test(item.id) || ids.has(item.id) || !shortText(item.label, 500) || !["single", "multi", "text", "password"].includes(item.type)) return null;
      const options = item.options ?? [];
      if (!Array.isArray(options) || options.length > maximumOptions || !options.every(value => shortText(value, 300)) || new Set(options).size !== options.length || (!["text", "password"].includes(item.type) && options.length < 2) || (["text", "password"].includes(item.type) && options.length)) return null;
      if (item.sensitive !== undefined && typeof item.sensitive !== "boolean") return null;
      ids.add(item.id);
      questions.push({ id: item.id, label: item.label, type: item.type, options, ...(item.sensitive === true ? {sensitive: true} : {}) });
    }
    return { version: 1, title: data.title, questions };
  } catch { return null; }
}
type FencedBlock = { start: number; end: number; language: string; body: string };
function fencedBlocks(text: string): FencedBlock[] {
  return [...text.matchAll(/(?:^|\n)[ \t]*(`{3,}|~{3,})([^\n]*)\n([\s\S]*?)(?:\n[ \t]*\1[ \t]*(?=\n|$)|$)/g)].map(match => ({
    start: match.index!, end: match.index! + match[0].length,
    language: match[2].trim().toLowerCase(), body: match[3],
  }));
}
function inputBlock(text: string, maximumOptions = 8): InputBlock | null {
  const blocks: InputBlock[] = [];
  const codeBlocks = fencedBlocks(text);
  for (const block of codeBlocks) {
    if (!["harness-input", "harness", "json", ""].includes(block.language)) continue;
    const input = validateInput(block.body, maximumOptions);
    if (input) blocks.push({start: block.start, end: block.end, input});
  }
  // Models sometimes omit the fence. Only accept a complete, bounded protocol
  // object beginning on its own line, outside other code blocks.
  const starts = /(?:^|\n)[ \t]*(?=\{)/g;
  let start: RegExpExecArray | null;
  while ((start = starts.exec(text))) {
    const offset = start.index + start[0].length;
    starts.lastIndex = offset + 1;
    if (codeBlocks.some(block => offset >= block.start && offset < block.end)) continue;
    let depth = 0, quoted = false, escaped = false;
    for (let i = offset; i < Math.min(text.length, offset + 16_000); i++) {
      const char = text[i];
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === "{") depth++;
      else if (char === "}" && --depth === 0) {
        const input = validateInput(text.slice(offset, i + 1), maximumOptions);
        if (input) blocks.push({start: offset, end: i + 1, input});
        starts.lastIndex = i + 1;
        break;
      }
    }
  }
  return blocks.length === 1 ? blocks[0] : null;
}
export function parseConversationInput(text: string): ConversationInput | null {
  return inputBlock(text)?.input ?? null;
}
export function conversationInputDisplay(text: string, streaming = false): string {
  // Display is more tolerant than the actionable card: preserve every authored
  // option when a model exceeds the interactive choice limit.
  const block = inputBlock(text, 32);
  if (block) return text.slice(0, block.start) + [block.input.title, ...block.input.questions.map(q => `${q.label}${q.options.length ? `（${q.options.join(" / ")}）` : ""}`)].join("\n\n") + text.slice(block.end);
  // Invalid internal forms must not become code widgets. Keep ordinary code
  // literal, and retain a readable recovery path after an interrupted response.
  const internal = fencedBlocks(text).filter(block => ["harness-input", "harness"].includes(block.language));
  if (internal.length) {
    const replacement = streaming ? "" : "\n表单信息未能识别，请直接输入你的选择或补充信息。";
    return internal.reduceRight((value, block) => value.slice(0, block.start) + replacement + value.slice(block.end), text).trimEnd();
  }
  if (streaming) {
    const start = /(?:^|\n)```harness(?:-input)?(?:\s|$)/.exec(text)
      ?? /(?:^|\n)[ \t]*\{\s*"version"\s*:\s*1\s*,\s*"title"\s*:\s*"[^"\n]*"\s*,\s*"questions"\s*:\s*\[/.exec(text);
    if (start) return text.slice(0, start.index);
  }
  return text;
}
export function latestConversationInput(messages: readonly { id: string; role: string; content: readonly { type: string; text?: string }[]; status?: {type: string} }[]) {
  const last = messages.at(-1);
  if (!last || last.role !== "assistant" || (last.status && last.status.type !== "complete")) return null;
  const input = parseConversationInput(last.content.filter(p => p.type === "text").map(p => p.text ?? "").join("\n"));
  return input ? { messageId: last.id, input } : null;
}
export function formatConversationAnswers(input: ConversationInput, answers: Record<string, { selected: string[]; text: string }>, skipMissing = false): string | null {
  const lines: string[] = [];
  for (const question of input.questions) {
    const answer = answers[question.id];
    const selected = answer?.selected.filter(value => question.options.includes(value)) ?? [];
    const secret = isSensitiveQuestion(question);
    const text = secret ? answer?.text.slice(0, 4000) : answer?.text.trim().slice(0, 4000);
    const values = [...(["text", "password"].includes(question.type) ? [] : selected.slice(0, question.type === "single" ? 1 : 8)), text].filter(Boolean);
    if (!values.length && !skipMissing) return null;
    const sourceLabel = secret ? question.label.replace(/\s*\n\s*/g, " ") : question.label;
    const label = secret && !isSecretFieldLabel(sourceLabel) ? `${sourceLabel}（敏感信息）` : sourceLabel;
    lines.push(`${label}\n${values.join("；") || "已跳过"}`);
  }
  return `${input.title}\n\n${lines.join("\n\n")}${skipMissing ? "\n\n跳过未回答的问题，请基于已有信息继续；必要信息缺失时说明限制。" : ""}`;
}
