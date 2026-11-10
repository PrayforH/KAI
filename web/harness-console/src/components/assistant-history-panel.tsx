"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { locateInChat as locateMessage } from "../lib/chat-locate";
import { loadFullThreadHistory } from "../lib/task-history";
import { formatBytes } from "./artifact-list";
import styles from "./assistant-history-panel.module.css";

export type HistoryTab = "all" | "files" | "media";
type ContentPart = { type?: string; text?: string; source?: { value?: string }; metadata?: { filename?: string } };
export type HistoryMessage = { id: string; role: string; content: string | ContentPart[] };
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
export type HistoryEntry = { id: string; role: string; text: string; files: HistoryFile[] };

type ArtifactRecord = {
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


function fileHref(file: HistoryFile, threadId: string): string {
  if (file.inlineHref) return file.inlineHref;
  const query = new URLSearchParams({ preview: "1", thread_id: threadId });
  return `/api/harness/artifacts/${encodeURIComponent(file.artifactId ?? "")}?${query}`;
}

function openFile(file: HistoryFile) {
  if (!file.artifactId || typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("harness:preview-artifact", {
    detail: {
      artifact_id: file.artifactId,
      name: file.name,
      media_type: file.mediaType,
      size_bytes: file.sizeBytes,
      thread_id: null,
    },
  }));
}

/** 聊天记录抽屉：tabs、搜索、发送人/类型/日期筛选，每条记录都能回到原消息。 */
export function AssistantHistoryPanel({ open, threadId, assistantName = "助理", onClose }: {
  open: boolean; threadId: string; assistantName?: string; onClose: () => void;
}) {
  const [data, setData] = useState<{ entries: HistoryEntry[]; files: HistoryFile[] } | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<HistoryTab>("all");
  const [query, setQuery] = useState("");
  const [sender, setSender] = useState("all");
  const [kind, setKind] = useState("all");
  const [date, setDate] = useState("all");
  const [notice, setNotice] = useState("");
  const [reload, setReload] = useState(0);
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setData(null); setError("");
    const query = new URLSearchParams({ thread_id: threadId, limit: "200" });
    void Promise.all([
      loadFullThreadHistory(threadId),
      fetch(`/api/harness/artifacts?${query}`, { cache: "no-store", signal: controller.signal })
        .then(requireAuthenticatedResponse)
        .then(async response => {
          if (!response.ok) throw new Error("文件暂时无法读取");
          return await response.json() as ArtifactRecord[];
        }),
    ]).then(([messages, artifacts]) => {
      if (controller.signal.aborted) return;
      setData(historyEntries(messages as HistoryMessage[], artifacts.filter(item => item.thread_id === threadId)));
    }).catch(cause => {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error && cause.message ? cause.message : "聊天记录暂时无法读取");
    });
    return () => controller.abort();
  }, [open, threadId, reload]);

  useEffect(() => {
    if (!open) return;
    closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open, onClose]);

  const entries = useMemo(() => data ? filterEntries(data.entries, { query, sender }) : [], [data, query, sender]);
  const files = useMemo(() => data ? filterFiles(data.files, { query, sender, kind, date }) : [], [data, query, sender, kind, date]);
  const media = useMemo(() => files.filter(isMedia), [files]);
  const days = useMemo(() => [...new Set((data?.files ?? []).map(file => dayOf(file.createdAt)).filter(Boolean))], [data]);
  if (!open) return null;

  function switchTab(next: HistoryTab) {
    setTab(next); setQuery(""); setSender("all"); setKind("all"); setDate("all"); setNotice("");
  }

  function locate(messageId: string | null) {
    if (!messageId) { setNotice("这条记录没有关联消息"); return; }
    setNotice("");
    void locateMessage(messageId, threadId).then(found => {
      if (!found) setNotice("未在当前会话中找到这条消息");
    }).catch(() => setNotice("未能加载更早的消息，请重试"));
  }

  const list = tab === "all" ? entries : tab === "files" ? files : media;
  const empty = data === null || list.length > 0 ? "" : tab === "media"
    ? (data.files.some(isMedia) ? "没有符合筛选条件的记录" : "暂无图片或视频")
    : tab === "files"
      ? (data.files.length ? "没有符合筛选条件的记录" : "暂无文件")
      : (data.entries.length ? "没有符合筛选条件的记录" : "还没有聊天记录");

  return <aside className={styles.drawer} aria-label="聊天记录">
    <header className={styles.head}>
      <strong>聊天记录</strong>
      <button ref={closeButton} type="button" className={styles.close} aria-label="关闭聊天记录" onClick={onClose}>×</button>
    </header>
    <div className={styles.tabs} role="tablist" aria-label="记录类型">
      {([["all", "全部"], ["files", "文件"], ["media", "图片与视频"]] as const).map(([key, label]) =>
        <button type="button" key={key} role="tab" aria-selected={tab === key}
          className={tab === key ? styles.tabActive : undefined}
          onClick={() => switchTab(key)}>{label}</button>)}
    </div>
    <label className={styles.search}>
      <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="9" cy="9" r="6" /><path d="m14 14 4 4" /></svg>
      <input value={query} onChange={event => setQuery(event.target.value)}
        disabled={tab === "media"}
        placeholder={tab === "files" ? "搜索文件名称" : tab === "media" ? "暂不支持搜索图中文字" : "搜索聊天记录"}
        aria-label="搜索聊天记录" />
    </label>
    <div className={styles.filters}>
      <select aria-label="按发送人筛选" value={sender} onChange={event => setSender(event.target.value)}>
        <option value="all">发送人</option>
        <option value="user">我</option>
        <option value="assistant">{assistantName}</option>
      </select>
      {tab === "files" && <select aria-label="按文件类型筛选" value={kind} onChange={event => setKind(event.target.value)}>
        <option value="all">类型</option>
        <option value="image">图片</option>
        <option value="video">视频</option>
        <option value="pdf">PDF</option>
        <option value="html">HTML</option>
        <option value="zip">压缩包</option>
        <option value="other">其他</option>
      </select>}
      {tab !== "all" && <select aria-label="按日期筛选" value={date} onChange={event => setDate(event.target.value)}>
        <option value="all">日期</option>
        {days.map(day => <option key={day} value={day}>{day}</option>)}
      </select>}
    </div>
    <div className={styles.body}>
      {error && <p role="alert" className={styles.error}>{error}
        <button type="button" className={styles.link} onClick={() => setReload(value => value + 1)}>重试</button></p>}
      {!error && data === null && <p className={styles.hint}>正在读取聊天记录…</p>}
      {empty && <p className={styles.hint}>{empty}</p>}
      {notice && <p role="status" className={styles.notice}>{notice}</p>}

      {tab === "all" && entries.map(entry => <article key={entry.id} className={styles.msg}>
        <div className={styles.meta}>
          <span className={styles.sender}>{entry.role === "user" ? "我" : assistantName}</span>
          <button type="button" className={styles.locate} onClick={() => locate(entry.id)}>在聊天中定位</button>
        </div>
        {entry.text && <p className={styles.text}>{entry.text}</p>}
        {entry.files.length > 0 && <div className={styles.chips}>
          {entry.files.map(file => <button type="button" key={file.id} className={styles.chip}
            title={file.name} onClick={() => locate(file.messageId)}>
            <span className={styles.glyph} data-kind={fileKindOf(file.name, file.mediaType)}>{fileGlyph(file.name, file.mediaType)}</span>
            <span className={styles.chipName}>{file.name}</span>
          </button>)}
        </div>}
      </article>)}

      {tab === "files" && files.map(file => <article key={file.id} className={styles.fileRow}>
        <span className={styles.glyph} data-kind={fileKindOf(file.name, file.mediaType)}>{fileGlyph(file.name, file.mediaType)}</span>
        <span className={styles.fileMain}>
          <button type="button" className={styles.fileName} title={file.name}
            onClick={() => file.artifactId ? openFile(file) : undefined}>{file.name}</button>
          <span className={styles.fileMeta}>
            {file.origin === "user" ? "我上传" : assistantName}
            {file.sizeBytes ? ` · ${formatBytes(file.sizeBytes)}` : ""}
            {file.createdAt ? ` · ${dayOf(file.createdAt)}` : ""}
          </span>
        </span>
        <span className={styles.fileActions}>
          <button type="button" className={styles.locate} disabled={!file.messageId}
            onClick={() => locate(file.messageId)}>在聊天中定位</button>
          <a className={styles.link} href={fileHref(file, threadId)} target="_blank" rel="noreferrer">打开</a>
        </span>
      </article>)}

      {tab === "media" && <div className={styles.grid}>
        {media.map(file => {
          const kind = fileKindOf(file.name, file.mediaType);
          return <figure key={file.id} className={styles.mediaItem}>
            <button type="button" className={styles.mediaButton} title={file.name}
              onClick={() => file.artifactId ? openFile(file) : undefined}>
              {kind === "image"
                ? <img src={fileHref(file, threadId)} alt={file.name} loading="lazy" />
                : <span className={styles.video} aria-hidden="true">▶</span>}
            </button>
            <figcaption>
              <span className={styles.chipName}>{file.name}</span>
              <button type="button" className={styles.locate} disabled={!file.messageId}
                onClick={() => locate(file.messageId)}>在聊天中定位</button>
            </figcaption>
          </figure>;
        })}
      </div>}
    </div>
  </aside>;
}
