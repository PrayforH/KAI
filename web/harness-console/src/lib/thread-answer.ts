import { parseConversationInput, type ConversationInput } from "./conversation-input";

export type ThreadAsk = { messageId: string; input: ConversationInput };

type HistoryMessage = {
  id?: string;
  role?: string;
  content?: unknown;
};

function messageText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => (part && typeof part === "object" && typeof (part as { text?: unknown }).text === "string" ? (part as { text: string }).text : ""))
      .join("\n");
  }
  return "";
}

/**
 * 线程里是否还挂着一个待回答的 ask（末条 assistant 文本是合法 harness-input，
 * 且其后没有用户消息）。tool/activity 消息不影响判定。
 */
export function pendingThreadAsk(messages: readonly HistoryMessage[]): ThreadAsk | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (!message || typeof message.role !== "string") continue;
    if (message.role === "user") return null;
    if (message.role !== "assistant") continue;
    const input = parseConversationInput(messageText(message.content));
    if (input) return { messageId: String(message.id ?? `assistant-${index}`), input };
  }
  return null;
}

async function drain(response: Response): Promise<void> {
  const body = response.body;
  if (!body) return;
  try {
    const reader = body.getReader();
    while (true) {
      const { done } = await reader.read();
      if (done) break;
    }
  } catch {
    // 流式响应中断不影响"提交已送达"：run 已在服务端创建。
  }
}

/**
 * 把主会话里的回答写进目标子线程：走既有 AG-UI run 入口，消息落在该 threadId 上，
 * 由 worker 继续执行该线程（上下文由其自身历史重建）。不写主助理线程。
 */
export async function submitThreadAnswer(input: {
  threadId: string;
  agentName: string;
  agentVersion: string;
  agentOwnerUserId?: string;
  spaceId?: string | null;
  text: string;
}): Promise<void> {
  const runId = typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `run-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const query = new URLSearchParams({
    agent_name: input.agentName,
    agent_version: input.agentVersion,
  });
  if (input.agentOwnerUserId) query.set("agent_owner_user_id", input.agentOwnerUserId);
  if (input.spaceId) query.set("space_id", input.spaceId);
  const response = await fetch(`/api/agui?${query.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    cache: "no-store",
    body: JSON.stringify({
      threadId: input.threadId,
      runId,
      messages: [
        {
          id: `msg_${runId}`,
          role: "user",
          content: input.text,
        },
      ],
      state: null,
      tools: [],
      context: [],
      // 投给助理线程的消息也属于助理范围：漏了这个标记，运行时就不给它装配
      // 平台联网，被 @ 的 Bot 只能回「取不到」。
      forwardedProps: { conversationSurface: "assistant", assistantScope: true, appendToHistory: true },
    }),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail.trim() || `提交失败（${response.status}）`);
  }
  // 后台读完事件流，避免中途断开把刚创建的执行取消掉；调用方无需等待。
  void drain(response);
}
