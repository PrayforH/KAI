/**
 * 聊天记录的文件归类与筛选：单助理（artifacts + 输入附件）和群聊（群消息附件）
 * 共用这一份实现，避免两个面板各写一套分类规则后越走越偏。
 */

export type HistoryTab = "all" | "files" | "media";
export type ContentPart = { type?: string; text?: string; source?: { value?: string }; metadata?: { filename?: string } };
export type HistoryMessage = { id: string; role: string; content: string | ContentPart[] };

/** One file the conversation produced, or one attachment the user sent. */
export type HistoryFile = {
  id: string;
  name: string;
  mediaType: string;
  sizeBytes: number | null;
  /** Artifacts carry the run's time; attachments have none in history. */
  createdAt: string | null;
  origin: "user" | "assistant";
  /** The message the file belongs to, so 在聊天中定位 can land on it. */
  messageId: string | null;
  /** Harness artifact id (output files) — opens in the shared preview dialog. */
  artifactId: string | null;
  /** Inline URL for input attachments (user-uploaded files). */
  inlineHref: string | null;
  downloadHref: string | null;
};

/** 一条记录；label 用于群聊显示具体是谁发的。 */
export type HistoryEntry = { id: string; role: string; text: string; files: HistoryFile[]; label?: string };

export type ArtifactRecord = {
  artifact_id: string; run_id: string; thread_id?: string | null;
  name?: string | null; media_type?: string | null;
  size_bytes?: number | null; created_at?: string | null;
};

/** Same convention agent-thread uses to attach a live run to its message. */
export function messageOwnsArtifact(messageId: string, runId: string) {
  const prefix = `assistant-${runId}`;
  return messageId === prefix || messageId.startsWith(`${prefix}-`);
}

function messageText(content: HistoryMessage["content"]): string {
  if (typeof content === "string") return content;
  return content.filter(part => part.type === "text" || typeof part.text === "string")
    .map(part => part.text ?? "").join("\n");
}

function messageAttachments(content: HistoryMessage["content"]): ContentPart[] {
  if (typeof content === "string") return [];
  return content.filter(part => part.type !== "text" && part.source?.value);
}

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|avif|svg|bmp)$/i;
const VIDEO_EXT = /\.(mp4|mov|webm|m4v|avi|mkv)$/i;

export function fileKindOf(name: string, mediaType = ""): "image" | "video" | "pdf" | "html" | "zip" | "other" {
  const type = mediaType.toLowerCase();
  if (type.startsWith("image/") || IMAGE_EXT.test(name)) return "image";
  if (type.startsWith("video/") || VIDEO_EXT.test(name)) return "video";
  if (type === "application/pdf" || /\.pdf$/i.test(name)) return "pdf";
  if (type === "text/html" || /\.html?$/i.test(name)) return "html";
  if (type.includes("zip") || /\.(zip|tar|gz|tgz|7z)$/i.test(name)) return "zip";
  return "other";
}

/** Short badge text drawn on the prototype's colored file glyph. */
export function fileGlyph(name: string, mediaType = ""): string {
  const kind = fileKindOf(name, mediaType);
  if (kind === "image") return "IMG";
  if (kind === "video") return "VID";
  if (kind === "pdf") return "PDF";
  if (kind === "html") return "HTML";
  if (kind === "zip") return "ZIP";
  const extension = /\.([a-z0-9]{1,4})$/i.exec(name)?.[1];
  return extension ? extension.toUpperCase() : "FILE";
}

/**
 * Merge the thread's messages with the files it produced. Artifacts carry the
 * run id, so they attach to the assistant message that answered that run and
 * 在聊天中定位 can jump straight back to the reply that delivered the file.
 */
export function historyEntries(messages: HistoryMessage[], artifacts: ArtifactRecord[]): {
  entries: HistoryEntry[]; files: HistoryFile[];
} {
  const entries: HistoryEntry[] = [];
  for (const message of messages) {
    const files: HistoryFile[] = messageAttachments(message.content).flatMap(part => {
      const inputId = part.source?.value;
      if (!inputId) return [];
      const name = part.metadata?.filename ?? "附件";
      return [{
        id: `input:${inputId}`,
        name,
        mediaType: "",
        sizeBytes: null,
        createdAt: null,
        origin: "user" as const,
        messageId: message.id,
        artifactId: null,
        inlineHref: `/api/input-artifacts/${encodeURIComponent(inputId)}/content?inline=1`,
        downloadHref: `/api/input-artifacts/${encodeURIComponent(inputId)}/content`,
      }];
    });
    const text = messageText(message.content);
    if (!text.trim() && files.length === 0) continue;
    entries.push({ id: message.id, role: message.role, text, files });
  }
  const ownerIds = new Map(entries.map(entry => [entry.id, entry]));
  for (const artifact of artifacts) {
    const owner = messages.find(message => messageOwnsArtifact(message.id, artifact.run_id));
    const file: HistoryFile = {
      id: `artifact:${artifact.artifact_id}`,
      name: artifact.name || "未命名文件",
      mediaType: artifact.media_type || "application/octet-stream",
      sizeBytes: artifact.size_bytes ?? null,
      createdAt: artifact.created_at ?? null,
      origin: "assistant",
      messageId: owner?.id ?? null,
      artifactId: artifact.artifact_id,
      inlineHref: null,
      downloadHref: `/api/harness/artifacts/${encodeURIComponent(artifact.artifact_id)}`,
    };
    const entry = owner ? ownerIds.get(owner.id) : undefined;
    if (entry) entry.files.push(file);
    else entries.push({ id: `artifact:${artifact.artifact_id}`, role: "assistant", text: "", files: [file] });
  }
  const files = entries.flatMap(entry => entry.files).sort((a, b) => {
    const left = a.createdAt ? Date.parse(a.createdAt) : 0;
    const right = b.createdAt ? Date.parse(b.createdAt) : 0;
    return right - left;
  });
  return { entries, files };
}

export function senderOf(role: string): "user" | "assistant" {
  return role === "user" ? "user" : "assistant";
}

export function filterEntries(entries: HistoryEntry[], { query, sender }: { query: string; sender: string }) {
  const needle = query.trim().toLowerCase();
  return entries.filter(entry => (sender === "all" || senderOf(entry.role) === sender)
    && (!needle || `${entry.text}\n${entry.files.map(file => file.name).join("\n")}`.toLowerCase().includes(needle)));
}

export function filterFiles(files: HistoryFile[], { query, sender, kind, date }: {
  query: string; sender: string; kind?: string; date?: string;
}) {
  const needle = query.trim().toLowerCase();
  return files.filter(file => (sender === "all" || file.origin === sender)
    && (!kind || kind === "all" || fileKindOf(file.name, file.mediaType) === kind)
    && (!date || date === "all" || dayOf(file.createdAt) === date)
    && (!needle || file.name.toLowerCase().includes(needle)));
}

export function isMedia(file: HistoryFile) {
  const kind = fileKindOf(file.name, file.mediaType);
  return kind === "image" || kind === "video";
}

export function dayOf(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, "0")}/${String(date.getDate()).padStart(2, "0")}`;
}
