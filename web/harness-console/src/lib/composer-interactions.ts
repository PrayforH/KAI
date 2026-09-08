import type { Attachment, CompleteAttachment } from "@assistant-ui/react";
import { inputArtifactIdFromAttachment } from "./input-attachment-adapter";

export const COMPOSER_COMMANDS = [
  { name: "/new", label: "新建任务", description: "当前输入会保留为原任务草稿" },
  { name: "/stop", label: "停止运行", description: "停止当前任务，并暂停后续队列" },
  { name: "/files", label: "任务文件", description: "打开右侧文件分栏" },
  { name: "/clear", label: "清空输入", description: "保留对话历史与队列" },
  { name: "/help", label: "输入帮助", description: "/ 命令 · @ 智能体 · $ 技能" },
];
export function composerTrigger(text: string, caret: number) {
  const before = text.slice(0, caret);
  const match = /(?:^|\s)([/@$])([^\s/@$]*)$/u.exec(before);
  if (!match || (match[1] === "/" && before.trimStart() !== match[0].trimStart())) return null;
  return { symbol: match[1], query: match[2].toLowerCase(), start: caret - match[2].length - 1, end: caret };
}
export interface QueuedPrompt { id: string; text: string; attachments: CompleteAttachment[]; steerRunId?: string }
export function queueAttachments(attachments: readonly Attachment[]): CompleteAttachment[] {
  return attachments.map((attachment) => {
    if (attachment.status.type === "complete") return attachment as CompleteAttachment;
    const id = inputArtifactIdFromAttachment(attachment);
    if (!id || attachment.status.type !== "requires-action") throw new Error(`请等待 ${attachment.name} 上传完成，或移除失败附件。`);
    const mimeType = attachment.contentType || "application/octet-stream";
    return { id, type: attachment.type, name: attachment.name, contentType: mimeType,
      status: { type: "complete" }, content: [{ type: "file", data: id, mimeType, filename: attachment.name }] };
  });
}
export function queueMayDispatch(busy: boolean, paused: boolean, phase?: string) {
  return !busy && !paused && !["running", "queued", "waiting_approval"].includes(phase ?? "");
}
export function restorePromptQueue(raw: string | null): QueuedPrompt[] {
  try {
    const value: unknown = JSON.parse(raw ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is QueuedPrompt => Boolean(item && typeof item.id === "string" && typeof item.text === "string" && Array.isArray(item.attachments) && item.attachments.every((a: CompleteAttachment) => a?.status?.type === "complete" && Array.isArray(a.content)))).slice(0, 50);
  } catch { return []; }
}
