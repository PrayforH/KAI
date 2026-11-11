"use client";

import {
  memo, startTransition, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState,
} from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { locateInChat as locateMessage } from "../lib/chat-locate";
import {
  clearHistoryIndex,
  dayOf,
  fileGlyph,
  fileKindOf,
  filterEntries,
  filterFiles,
  historyEntries,
  isMedia,
  readHistoryIndex,
  writeHistoryIndex,
  type ArtifactRecord,
  type HistoryEntry,
  type HistoryFile,
  type HistoryIndex,
  type HistoryMessage,
  type HistoryTab,
} from "../lib/history-index";
import { loadFullThreadHistory } from "../lib/task-history";
import { formatBytes } from "./artifact-list";
import styles from "./assistant-history-panel.module.css";

// The pure index lives in lib/history-index so it can be cached and tested
// without React. Re-exported here because this component is its public face.
export {
  dayOf, fileGlyph, fileKindOf, filterEntries, filterFiles, historyEntries, isMedia,
} from "../lib/history-index";
export type { HistoryEntry, HistoryFile, HistoryMessage, HistoryTab } from "../lib/history-index";

const SEARCH_DEBOUNCE_MS = 120;
const STICK_TO_BOTTOM_PX = 48;

/** Keep a filter box responsive without dropping the last keystroke. */
function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
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

function errorText(cause: unknown): string {
  return cause instanceof Error && cause.message ? cause.message : "聊天记录暂时无法读取";
}

const MessageRow = memo(function MessageRow({ entry, assistantName, locating, busy, onLocate }: {
  entry: HistoryEntry; assistantName: string; locating: boolean; busy: boolean;
  onLocate: (messageId: string | null) => void;
}) {
  // A file whose run fell outside the loaded history has no message to land
  // on; only offer 定位 when the row really points at one.
  const targetId = entry.id.startsWith("artifact:")
    ? entry.files.find(file => file.messageId)?.messageId ?? null
    : entry.id;
  return <article className={styles.msg}>
    <div className={styles.meta}>
      <span className={styles.sender}>{entry.role === "user" ? "我" : assistantName}</span>
      <button type="button" className={styles.locate} disabled={!targetId || busy}
        onClick={() => onLocate(targetId)}>{locating ? "正在定位…" : "在聊天中定位"}</button>
    </div>
    {entry.text && <p className={styles.text}>{entry.text}</p>}
    {entry.files.length > 0 && <div className={styles.chips}>
      {entry.files.map(file => <button type="button" key={file.id} className={styles.chip}
        title={file.name} disabled={busy} onClick={() => onLocate(file.messageId)}>
        <span className={styles.glyph} data-kind={fileKindOf(file.name, file.mediaType)}>{fileGlyph(file.name, file.mediaType)}</span>
        <span className={styles.chipName}>{file.name}</span>
      </button>)}
    </div>}
  </article>;
});

const FileRow = memo(function FileRow({ file, threadId, assistantName, locating, busy, onLocate }: {
  file: HistoryFile; threadId: string; assistantName: string; locating: boolean; busy: boolean;
  onLocate: (messageId: string | null) => void;
}) {
  return <article className={styles.fileRow}>
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
      <button type="button" className={styles.locate} disabled={!file.messageId || busy}
        onClick={() => onLocate(file.messageId)}>{locating ? "正在定位…" : "在聊天中定位"}</button>
      <a className={styles.link} href={fileHref(file, threadId)} target="_blank" rel="noreferrer">打开</a>
    </span>
  </article>;
});

const MediaItem = memo(function MediaItem({ file, threadId, locating, busy, onLocate }: {
  file: HistoryFile; threadId: string; locating: boolean; busy: boolean;
  onLocate: (messageId: string | null) => void;
}) {
  const kind = fileKindOf(file.name, file.mediaType);
  return <figure className={styles.mediaItem}>
    <button type="button" className={styles.mediaButton} title={file.name}
      onClick={() => file.artifactId ? openFile(file) : undefined}>
      {kind === "image"
        ? <img src={fileHref(file, threadId)} alt={file.name} loading="lazy" />
        : <span className={styles.video} aria-hidden="true">▶</span>}
    </button>
    <figcaption>
      <span className={styles.chipName}>{file.name}</span>
      <button type="button" className={styles.locate} disabled={!file.messageId || busy}
        onClick={() => onLocate(file.messageId)}>{locating ? "正在定位…" : "在聊天中定位"}</button>
    </figcaption>
  </figure>;
});

/** 聊天记录抽屉：tabs、搜索、发送人/类型/日期筛选，每条记录都能回到原消息。 */
export function AssistantHistoryPanel({ open, threadId, assistantName = "助理", onClose }: {
  open: boolean; threadId: string; assistantName?: string; onClose: () => void;
}) {
  const [data, setData] = useState<HistoryIndex | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<HistoryTab>("all");
  const [query, setQuery] = useState("");
  const [sender, setSender] = useState("all");
  const [kind, setKind] = useState("all");
  const [date, setDate] = useState("all");
  const [notice, setNotice] = useState("");
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  const [locatingId, setLocatingId] = useState<string | null>(null);
  const [locatePages, setLocatePages] = useState(0);
  const closeButton = useRef<HTMLButtonElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const lastScrollHeight = useRef(0);
  const debouncedQuery = useDebouncedValue(query, SEARCH_DEBOUNCE_MS);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError(""); setNotice("");
    // A thread read moments ago is reused as-is; opening the drawer is a state
    // toggle, not a reason to page the whole thread in again.
    const cached = readHistoryIndex(threadId);
    setData(cached);
    setLoadingMore(false);
    if (cached) return () => controller.abort();

    const pages: HistoryMessage[][] = [];
    let artifacts: ArtifactRecord[] = [];
    let latestIndex: HistoryIndex | null = null;
    const publish = () => {
      // Wait for the first page: artifacts alone would flash "no history".
      if (controller.signal.aborted || pages.length === 0) return;
      // Pages arrive newest-first; render the thread in reading order so an
      // older page prepends instead of landing after the newest message.
      const messages = pages.slice().reverse().flat();
      latestIndex = historyEntries(messages, artifacts.filter(item => item.thread_id === threadId));
      startTransition(() => setData(latestIndex));
    };
    setLoadingMore(true);
    const artifactsQuery = new URLSearchParams({ thread_id: threadId, limit: "200" });
    // A failed file list is not a failed history read: keep the conversation on
    // screen, say which half failed, and skip the cache write so a later open
    // retries instead of reusing an index without files.
    let artifactsFailed = false;
    const artifactsRequest = fetch(`/api/harness/artifacts?${artifactsQuery}`, {
      cache: "no-store", signal: controller.signal,
    })
      .then(requireAuthenticatedResponse)
      .then(async response => {
        if (!response.ok) throw new Error("文件暂时无法读取");
        return await response.json() as ArtifactRecord[];
      })
      .then(list => { artifacts = list; publish(); })
      .catch(cause => {
        if (controller.signal.aborted) throw cause;
        artifactsFailed = true;
      });
    void Promise.all([
      loadFullThreadHistory(threadId, {
        signal: controller.signal,
        onPage: page => { pages.push(page.messages as HistoryMessage[]); publish(); },
      }).then(messages => {
        // Callers that only hand back the final list (older readers, mocks)
        // still get an index; paged callers already published every page.
        if (pages.length === 0) {
          pages.push(messages as HistoryMessage[]);
          publish();
        }
      }),
      artifactsRequest,
    ]).then(() => {
      if (controller.signal.aborted) return;
      publish();
      if (artifactsFailed) setNotice("文件列表暂时无法读取，聊天记录仍可查看");
      else if (latestIndex) writeHistoryIndex(threadId, latestIndex);
    }).catch(cause => {
      if (controller.signal.aborted) return;
      // The newest page may already be on screen; a failed older page should
      // not replace it with an error state.
      if (pages.length === 0) setError(errorText(cause));
      else setNotice(artifactsFailed ? "更早的记录和文件列表暂时无法读取" : "更早的记录暂时无法读取");
    }).finally(() => {
      if (!controller.signal.aborted) setLoadingMore(false);
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

  useEffect(() => {
    // A new thread (or reopening the drawer) starts at the newest message.
    lastScrollHeight.current = 0;
    stickToBottom.current = true;
  }, [open, threadId]);

  // Keep the reader's place while older pages prepend above the list, and stay
  // pinned to the newest message when that is where they were. While a search
  // narrows the list, neither applies: snapping to the bottom would throw the
  // reader out of the results they are scanning.
  const searching = debouncedQuery.trim().length > 0;
  useLayoutEffect(() => {
    const element = body.current;
    if (!element) return;
    if (searching) {
      lastScrollHeight.current = element.scrollHeight;
      return;
    }
    if (lastScrollHeight.current === 0 || stickToBottom.current) {
      element.scrollTop = element.scrollHeight;
    } else {
      const grew = element.scrollHeight - lastScrollHeight.current;
      if (grew > 0) element.scrollTop += grew;
    }
    lastScrollHeight.current = element.scrollHeight;
  }, [data, searching]);

  const entries = useMemo(
    () => data && tab === "all" ? filterEntries(data.entries, { query: debouncedQuery, sender }) : [],
    [data, tab, debouncedQuery, sender],
  );
  const files = useMemo(
    () => data && tab !== "all" ? filterFiles(data.files, { query: debouncedQuery, sender, kind, date }) : [],
    [data, tab, debouncedQuery, sender, kind, date],
  );
  const media = useMemo(() => files.filter(isMedia), [files]);
  const days = useMemo(() => [...new Set((data?.files ?? []).map(file => dayOf(file.createdAt)).filter(Boolean))], [data]);
  const locate = useCallback(async (messageId: string | null) => {
    if (!messageId) { setNotice("这条记录没有关联消息"); return; }
    // The walk is visible in two places: the clicked row says 正在定位… and this
    // drawer-level status keeps the real page count in view while older pages
    // are being fetched. Clearing the stale notice avoids two competing lines.
    setNotice("");
    setLocatePages(0);
    setLocatingId(messageId);
    try {
      const outcome = await locateMessage(messageId, threadId, {
        onProgress: () => setLocatePages(pages => pages + 1),
      });
      // "busy" only means a page is still in flight; it is not proof the
      // message is gone, so never turn it into "not found".
      setNotice(
        outcome === "located"
          ? ""
          : outcome === "retry"
            ? "聊天记录仍在加载，请稍后重试"
            : "未在当前会话中找到这条消息",
      );
    } catch {
      setNotice("未能加载更早的消息，请重试");
    } finally {
      setLocatingId(null);
      setLocatePages(0);
    }
  }, [threadId]);
  if (!open) return null;

  function switchTab(next: HistoryTab) {
    setTab(next); setQuery(""); setSender("all"); setKind("all"); setDate("all"); setNotice("");
  }

  function retry() {
    clearHistoryIndex(threadId);
    setReload(value => value + 1);
  }

  function onBodyScroll() {
    const element = body.current;
    if (!element) return;
    stickToBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < STICK_TO_BOTTOM_PX;
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
    {locatingId && <p className={styles.locateStatus} role="status" aria-live="polite">
      <span className={styles.spinner} aria-hidden="true" />
      <span>正在翻找更早的消息…</span>
      {locatePages > 0 && <span className={styles.locateCount} aria-hidden="true">已查 {locatePages} 页</span>}
    </p>}
    <div ref={body} className={styles.body} onScroll={onBodyScroll}>
      {loadingMore && data && !empty && <p className={styles.hint}>正在读取更早的记录…</p>}
      {error && <p role="alert" className={styles.error}>{error}
        <button type="button" className={styles.link} onClick={retry}>重试</button></p>}
      {!error && data === null && <p className={styles.hint}>正在读取聊天记录…</p>}
      {empty && <p className={styles.hint}>{empty}</p>}
      {notice && <p role="status" className={styles.notice}>{notice}</p>}

      {tab === "all" && entries.map(entry => <MessageRow key={entry.id} entry={entry}
        assistantName={assistantName} locating={locatingId === entry.id}
        busy={locatingId !== null} onLocate={locate} />)}

      {tab === "files" && files.map(file => <FileRow key={file.id} file={file} threadId={threadId}
        assistantName={assistantName} locating={locatingId === file.messageId}
        busy={locatingId !== null} onLocate={locate} />)}

      {tab === "media" && <div className={styles.grid}>
        {media.map(file => <MediaItem key={file.id} file={file} threadId={threadId}
          locating={locatingId === file.messageId}
          busy={locatingId !== null} onLocate={locate} />)}
      </div>}
    </div>
  </aside>;
}
