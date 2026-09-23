/** A display protocol only: submitted answers are ordinary user messages, never tool calls. */
export type ConversationQuestion = { id: string; label: string; type: "single" | "multi" | "text"; options: string[] };
export type ConversationInput = { version: 1; title: string; questions: ConversationQuestion[] };
function shortText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maximum;
}
type InputBlock = { start: number; end: number; input: ConversationInput };
function validateInput(raw: string): ConversationInput | null {
  if (raw.length > 16_000) return null;
  try {
    const data = JSON.parse(raw);
    if (!data || data.version !== 1 || !shortText(data.title, 160) || !Array.isArray(data.questions) || !data.questions.length || data.questions.length > 4) return null;
    if (Object.keys(data).some(key => !["version", "title", "questions"].includes(key))) return null;
    const ids = new Set<string>();
    const questions: ConversationQuestion[] = [];
    for (const item of data.questions) {
      if (!item || !shortText(item.id, 64) || !/^[\w-]+$/.test(item.id) || ids.has(item.id) || !shortText(item.label, 500) || !["single", "multi", "text"].includes(item.type)) return null;
      const options = item.options ?? [];
      if (!Array.isArray(options) || options.length > 8 || !options.every(value => shortText(value, 300)) || new Set(options).size !== options.length || (item.type !== "text" && options.length < 2) || (item.type === "text" && options.length)) return null;
      ids.add(item.id);
      questions.push({ id: item.id, label: item.label, type: item.type, options });
    }
    return { version: 1, title: data.title, questions };
  } catch { return null; }
}
function inputBlock(text: string): InputBlock | null {
  const blocks: InputBlock[] = [];
  const codeBlocks = [...text.matchAll(/```([^\n]*)\n([\s\S]*?)\n```/g)];
  for (const match of codeBlocks) {
    if (!["harness-input", "json", ""].includes(match[1].trim().toLowerCase())) continue;
    const input = validateInput(match[2]);
    if (input) blocks.push({start: match.index!, end: match.index! + match[0].length, input});
  }
  // Models sometimes omit the fence. Only accept a complete, bounded protocol
  // object beginning on its own line, outside other code blocks.
  const starts = /(?:^|\n)[ \t]*(?=\{)/g;
  let start: RegExpExecArray | null;
  while ((start = starts.exec(text))) {
    const offset = start.index + start[0].length;
    starts.lastIndex = offset + 1;
    if (codeBlocks.some(block => offset >= block.index! && offset < block.index! + block[0].length)) continue;
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
        const input = validateInput(text.slice(offset, i + 1));
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
  const block = inputBlock(text);
  if (block) return text.slice(0, block.start) + [block.input.title, ...block.input.questions.map(q => `${q.label}${q.options.length ? `（${q.options.join(" / ")}）` : ""}`)].join("\n\n") + text.slice(block.end);
  if (streaming) {
    const start = /(?:^|\n)```harness-input(?:\s|$)/.exec(text)
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
export function formatConversationAnswers(input: ConversationInput, answers: Record<string, { selected: string[]; text: string }>): string | null {
  const lines: string[] = [];
  for (const question of input.questions) {
    const answer = answers[question.id];
    const selected = answer?.selected.filter(value => question.options.includes(value)) ?? [];
    const values = [...(question.type === "text" ? [] : selected.slice(0, question.type === "single" ? 1 : 8)), answer?.text.trim().slice(0, 4000)].filter(Boolean);
    if (!values.length) return null;
    lines.push(`${question.label}\n${values.join("；")}`);
  }
  return `${input.title}\n\n${lines.join("\n\n")}`;
}
