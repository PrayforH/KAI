"use client";
import { z } from "zod";
import { AssistantEventLine } from "./assistant-event-line";

const sourceSchema = z.object({
  name: z.string().min(1).max(160),
  href: z.string().refine(value => /^\/(?:assistant\?|\?)/.test(value)),
  botId: z.string().max(128).optional(),
  color: z.string().regex(/^#[0-9a-f]{6}$/i).optional(),
  avatar: z.string().max(40).optional(),
  timestamp: z.string().datetime({ offset: true }).optional(),
  direction: z.enum(["sent", "received"]).optional(),
});
export function noticeSource(parts: readonly {type: string; toolName?: string; args?: unknown}[]) {
  const part = parts.find(item => item.type === "tool-call" && item.toolName === "harness_notice_source");
  const source = sourceSchema.safeParse(part?.args);
  return source.success ? source.data : null;
}
type NoticeMessage = { id: string; content: Parameters<typeof noticeSource>[0] };
export function noticeExchangeForMessage(messages: readonly NoticeMessage[], messageId: string) {
  const index = messages.findIndex(message => message.id === messageId);
  const source = index >= 0 ? noticeSource(messages[index].content) : null;
  if (!source?.timestamp || source.direction === "sent") return null;
  const near = (a: ReturnType<typeof noticeSource>, b: ReturnType<typeof noticeSource>) =>
    Boolean(a?.direction !== "sent" && b?.direction !== "sent" && a?.timestamp && b?.timestamp && Date.parse(b.timestamp) >= Date.parse(a.timestamp) &&
      Date.parse(b.timestamp) - Date.parse(a.timestamp) < 5 * 60_000 &&
      new Date(a.timestamp).toDateString() === new Date(b.timestamp).toDateString());
  if (index > 0 && near(noticeSource(messages[index - 1].content), source)) return { hidden: true, sources: [], count: 0 };
  const sources = [source];
  let count = 1;
  let previous = source;
  for (let next = index + 1; next < messages.length; next++) {
    const following = noticeSource(messages[next].content);
    if (!following || !near(previous, following)) break;
    previous = following;
    count += 1;
    if (!sources.some(item => item.href === following.href || item.botId && item.botId === following.botId)) sources.push(following);
  }
  return { hidden: false, sources, count };
}
export function AssistantNoticeSource({parts, exchange}: {parts: Parameters<typeof noticeSource>[0]; exchange?: ReturnType<typeof noticeExchangeForMessage>}) {
  const source = noticeSource(parts);
  if (!source) return null;
  if (source.direction === "sent") return <AssistantEventLine label="已发送消息给" {...source} />;
  if (exchange?.hidden) return null;
  if (exchange && exchange.count > 1) return <div className="assistant-exchange-line">
    <span>{exchange.count} 条消息往来：</span>
    {exchange.sources.map(item => <AssistantEventLine key={item.href} label="" {...item} />)}
  </div>;
  return <AssistantEventLine label="消息来自" {...source} />;
}
