"use client";
import { z } from "zod";
import { BotRailAvatar } from "./bot-rail";
import { assistantCopy } from "../lib/assistant-copy";

const sourceSchema = z.object({
  name: z.string().min(1).max(160),
  href: z.string().refine(value => /^\/(?:assistant\?|\?)/.test(value)),
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
  return <div className="assistant-message-source">
    <span>消息来自</span>
    <a href={source.href}>
      <BotRailAvatar tone={source.color ?? "#658b7d"} shape={source.avatar} />
      <span>{assistantCopy(source.name) || "后台助理"}</span>
    </a>
  </div>;
}
