/**
 * 聊天记录索引：把线程历史与它产出的文件合成抽屉要展示的行。
 *
 * 这里只做纯数据变换和缓存，不碰 React，也不发请求：
 * 抽屉负责取数、渲染与交互，重复打开时先命中缓存再决定要不要回源。
 */

export type HistoryTab = "all" | "files" | "media";
export type HistoryMessagePart = {
  type?: string;
  text?: string;
  source?: { value?: string };
  metadata?: { filename?: string };
};
export type HistoryMessage = { id: string; role: string; content: string | HistoryMessagePart[] };

/** One file the conversation produced, or one attachment the user sent. */
export type HistoryFile = {
  id: string;
  name: string;
  mediaType: string;
  sizeBytes: number | null;
  /** Artifacts carry the run's time; attachments have none in history. */
  createdAt: string | null;
  origin: "assistant" | "user";
  /** The message the file belongs to, so 在聊天中定位 can land on it. */
  messageId: string | null;
  /** Harness artifact id (output files) — opens in the shared preview dialog. */
  artifactId: string | null;
  /** Inline URL for input attachments (user-uploaded files). */
  inlineHref: string | null;
  downloadHref: string | null;
};
export type HistoryEntry = {
  id: string;
  role: string;
  text: string;
  files: HistoryFile[];
  /** Lower-cased id + text + file names, built once per entry. */
  searchText: string;
};

export type ArtifactRecord = {
  artifact_id: string; run_id: string; thread_id?: string | null;
  name?: string | null; media_type?: string | null;
  size_bytes?: number | null; created_at?: string | null;
};

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|avif|svg|bmp)$/i;
const VIDEO_EXT = /\.(mp4|mov|webm|m4v|avi|mkv)$/i;
const RENDERED_ROLES = new Set(["user", "assistant"]);

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

function messageAttachments(content: HistoryMessage["content"]): HistoryMessagePart[] {
  if (typeof content === "string") return [];
  return content.filter(part => part.type !== "text" && part.source?.value);
}

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

function searchable(text: string, files: HistoryFile[]): string {
  return [text, ...files.map(file => file.name)].join("\n").toLowerCase();
}

/**
 * Merge the thread's messages with the files it produced. Artifacts carry the
 * run id, so they attach to the assistant message that answered that run and
 * 在聊天中定位 can jump straight back to the reply that delivered the file.
 *
 * Only rows the conversation actually renders become history entries: history
 * also carries tool traces and notice sources, and advertising 定位 for a node
 * that never mounts is what made some rows look unlocatable.
 */
export function historyEntries(messages: HistoryMessage[], artifacts: ArtifactRecord[]): {
  entries: HistoryEntry[]; files: HistoryFile[];
} {
  const entries: HistoryEntry[] = [];
  const ownerByRun = new Map<string, HistoryMessage>();
  for (const message of messages) {
    if (!RENDERED_ROLES.has(message.role)) continue;
    if (message.id.startsWith("assistant-")) {
      ownerByRun.set(message.id.slice("assistant-".length), message);
    }
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
    entries.push({ id: message.id, role: message.role, text, files, searchText: searchable(text, files) });
  }
  const entryById = new Map(entries.map(entry => [entry.id, entry]));
  for (const artifact of artifacts) {
    // Exact `assistant-<run>` is the normal shape; the prefixed forms are rare,
    // so only those fall back to the linear scan.
    const owner = ownerByRun.get(artifact.run_id)
      ?? messages.find(message => messageOwnsArtifact(message.id, artifact.run_id));
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
    const entry = owner ? entryById.get(owner.id) : undefined;
    if (entry) {
      entry.files.push(file);
      entry.searchText = searchable(entry.text, entry.files);
    } else {
      entries.push({
        id: `artifact:${artifact.artifact_id}`,
        role: "assistant",
        text: "",
        files: [file],
        searchText: searchable("", [file]),
      });
    }
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
    && (!needle || entry.searchText.includes(needle)));
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

/**
 * A freshly loaded drawer is reused for a short while. Opening the same thread
 * again is a state toggle, not a reason to page the whole thread back in.
 */
export const HISTORY_INDEX_TTL_MS = 30_000;
const HISTORY_INDEX_CACHE_LIMIT = 4;

export type HistoryIndex = { entries: HistoryEntry[]; files: HistoryFile[] };
type CacheEntry = HistoryIndex & { receivedAt: number };

const indexCache = new Map<string, CacheEntry>();

/** The cached index when it is still young enough to trust, else null. */
export function readHistoryIndex(threadId: string, maxAge = HISTORY_INDEX_TTL_MS): HistoryIndex | null {
  const cached = indexCache.get(threadId);
  if (!cached || Date.now() - cached.receivedAt > maxAge) return null;
  indexCache.delete(threadId);
  indexCache.set(threadId, cached);
  return { entries: cached.entries, files: cached.files };
}

export function writeHistoryIndex(threadId: string, index: HistoryIndex): void {
  indexCache.delete(threadId);
  indexCache.set(threadId, { ...index, receivedAt: Date.now() });
  while (indexCache.size > HISTORY_INDEX_CACHE_LIMIT) {
    const oldest = indexCache.keys().next().value;
    if (typeof oldest !== "string") break;
    indexCache.delete(oldest);
  }
}

export function clearHistoryIndex(threadId?: string): void {
  if (threadId) indexCache.delete(threadId);
  else indexCache.clear();
}
