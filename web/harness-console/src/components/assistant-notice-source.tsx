"use client";
import { z } from "zod";
import { AssistantEventLine } from "./assistant-event-line";

const sourceSchema = z.object({
  name: z.string().min(1).max(160),
  href: z.string().refine(value => /^\/(?:assistant\?|\?)/.test(value)),
  botId: z.string().max(128).optional(),
  color: z.string().regex(/^#[0-9a-f]{6}$/i).optional(),
  avatar: z.string().max(40).optional(),
});
export function noticeSource(parts: readonly {type: string; toolName?: string; args?: unknown}[]) {
  const part = parts.find(item => item.type === "tool-call" && item.toolName === "harness_notice_source");
  const source = sourceSchema.safeParse(part?.args);
  return source.success ? source.data : null;
}
export function AssistantNoticeSource({parts}: {parts: Parameters<typeof noticeSource>[0]}) {
  const source = noticeSource(parts);
  if (!source) return null;
  return <AssistantEventLine label="消息来自" {...source} />;
}
