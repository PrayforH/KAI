/** A display protocol only: submitted answers are ordinary user messages, never tool calls. */
export type ConversationQuestion = { id: string; label: string; type: "single" | "multi" | "text"; options: string[] };
export type ConversationInput = { version: 1; title: string; questions: ConversationQuestion[] };
const fence = /(?:^|\n)```harness-input\s*\n([\s\S]*?)\n```[ \t]*(?=\n|$)/g;
function shortText(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maximum;
}
export function parseConversationInput(text: string): ConversationInput | null {
  const matches = [...text.matchAll(fence)];
  // The reserved marker is explicit. Tolerate explanatory prose after it; models
  // often append a sentence even when asked to put the card last.
  if (matches.length !== 1) return null;
  if (matches[0][1].length > 16_000) return null;
  try {
    const data = JSON.parse(matches[0][1]);
    if (data.version !== 1 || !shortText(data.title, 160) || !Array.isArray(data.questions) || !data.questions.length || data.questions.length > 4) return null;
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
export function conversationInputDisplay(text: string, streaming = false): string {
  const input = parseConversationInput(text);
  if (input) return text.replace(fence, () => "\n" + [input.title, ...input.questions.map(q => `${q.label}${q.options.length ? `（${q.options.join(" / ")}）` : ""}`)].join("\n\n"));
  // Don't flash half a JSON form while the model is still streaming it.
  if (streaming) {
    const start = /(?:^|\n)```harness-input(?:\s|$)/.exec(text);
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
