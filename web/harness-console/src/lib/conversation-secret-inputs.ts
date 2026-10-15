import { isSensitiveQuestion, parseConversationInput } from "./conversation-input";

type Message = { role: string; content: readonly { type: string; text?: string }[] };
/** Include bare replies only when the preceding form explicitly requested a
 * single secret, so ordinary messages never become password guesses. */
export function conversationSecretInputs(messages: readonly Message[]): string[] {
  const inputs: string[] = [];
  let previousAssistant = "";
  for (const message of messages) {
    const text = message.content.flatMap(part => part.type === "text" ? [part.text ?? ""] : []).join("\n");
    if (message.role === "assistant") { previousAssistant = text; continue; }
    if (message.role !== "user") continue;
    inputs.push(text);
    const request = parseConversationInput(previousAssistant);
    if (!request || text.startsWith(request.title + "\n") || /^(?:已跳过|跳过|不提供|暂不提供|没有密码)$/.test(text.trim())) continue;
    if (request.questions.length === 1 && isSensitiveQuestion(request.questions[0])) inputs.push("密码\n" + text);
    else if (/^[A-Za-z_][\w.-]*\/[^\s/]+$/.test(text) && request.questions.some(q => /用户名|账号|username/i.test(q.label))) inputs.push("账号\n" + text);
  }
  return inputs;
}
