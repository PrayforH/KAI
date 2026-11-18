"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { formatBytes } from "./artifact-list";
import {
  dayOf,
  fileGlyph,
  fileKindOf,
  filterEntries,
  filterFiles,
  isMedia,
  type HistoryEntry,
  type HistoryFile,
  type HistoryTab,
} from "../lib/history-files";
import styles from "./assistant-history-panel.module.css";

export type HistoryDrawerProps = {
  ariaLabel: string;
  assistantName: string;
  status: "loading" | "error" | "ready";
  error: string;
  entries: HistoryEntry[];
  files: HistoryFile[];
  /** 没有任何记录时的空态文案，单助理与群聊各自说各自的话。 */
  emptyEntriesText: string;
  searchPlaceholder: string;
  /** 文件在哪打开：单助理走 artifact 预览接口，群聊走资料库下载链接。 */
  hrefFor: (file: HistoryFile) => string | null;
  /** 点文件名时的动作；不传就只留「打开」链接。 */
  onOpenFile?: (file: HistoryFile) => void;
  /** 定位回原消息；返回 false 表示会话里找不到。 */
  onLocate: (messageId: string) => Promise<boolean> | boolean;
  onRetry: () => void;
  onClose: () => void;
};

/**
 * 聊天记录列的本体：tabs、搜索、发送人/类型/日期筛选、文件分类和图片网格。
 * 单助理与群聊共用这一份界面，差异只在数据来源和定位方式。
 */
export function HistoryDrawer({
  ariaLabel,
  assistantName,
  status,
  error,
  entries: allEntries,
  files: allFiles,
  emptyEntriesText,
  searchPlaceholder,
  hrefFor,
  onOpenFile,
  onLocate,
  onRetry,
  onClose,
}: HistoryDrawerProps) {
  const [tab, setTab] = useState<HistoryTab>("all");
  const [query, setQuery] = useState("");
  const [sender, setSender] = useState("all");
  const [kind, setKind] = useState("all");
  const [date, setDate] = useState("all");
  const [notice, setNotice] = useState("");
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  const entries = useMemo(() => filterEntries(allEntries, { query, sender }), [allEntries, query, sender]);
  const files = useMemo(() => filterFiles(allFiles, { query, sender, kind, date }), [allFiles, query, sender, kind, date]);
  const media = useMemo(() => files.filter(isMedia), [files]);
  const days = useMemo(() => [...new Set(allFiles.map(file => dayOf(file.createdAt)).filter(Boolean))], [allFiles]);

  function switchTab(next: HistoryTab) {
    setTab(next); setQuery(""); setSender("all"); setKind("all"); setDate("all"); setNotice("");
  }

  function locate(messageId: string | null) {
    if (!messageId) { setNotice("这条记录没有关联消息"); return; }
    setNotice("");
    try {
      void Promise.resolve(onLocate(messageId)).then(found => {
        if (!found) setNotice("未在当前会话中找到这条消息");
      }).catch(() => setNotice("未能加载更早的消息，请重试"));
    } catch {
      setNotice("未能加载更早的消息，请重试");
    }
  }

  const list = tab === "all" ? entries : tab === "files" ? files : media;
  const loading = status === "loading";
  const empty = loading || list.length > 0 ? "" : tab === "media"
    ? (allFiles.some(isMedia) ? "没有符合筛选条件的记录" : "暂无图片或视频")
    : tab === "files"
      ? (allFiles.length ? "没有符合筛选条件的记录" : "暂无文件")
      : (allEntries.length ? "没有符合筛选条件的记录" : emptyEntriesText);

  return <aside className={styles.drawer} aria-label={ariaLabel}>
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
        placeholder={tab === "files" ? "搜索文件名称" : tab === "media" ? "暂不支持搜索图中文字" : searchPlaceholder}
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
      {status === "error" && <p role="alert" className={styles.error}>{error}
        <button type="button" className={styles.link} onClick={onRetry}>重试</button></p>}
      {loading && <p className={styles.hint}>正在读取聊天记录…</p>}
      {empty && <p className={styles.hint}>{empty}</p>}
      {notice && <p role="status" className={styles.notice}>{notice}</p>}

      {tab === "all" && entries.map(entry => <article key={entry.id} className={styles.msg}>
        <div className={styles.meta}>
          <span className={styles.sender}>{entry.label ?? (entry.role === "user" ? "我" : assistantName)}</span>
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

      {tab === "files" && files.map(file => {
        const href = hrefFor(file);
        return <article key={file.id} className={styles.fileRow}>
          <span className={styles.glyph} data-kind={fileKindOf(file.name, file.mediaType)}>{fileGlyph(file.name, file.mediaType)}</span>
          <span className={styles.fileMain}>
            <button type="button" className={styles.fileName} title={file.name}
              onClick={() => onOpenFile?.(file)}>{file.name}</button>
            <span className={styles.fileMeta}>
              {file.origin === "user" ? "我上传" : assistantName}
              {file.sizeBytes ? ` · ${formatBytes(file.sizeBytes)}` : ""}
              {file.createdAt ? ` · ${dayOf(file.createdAt)}` : ""}
            </span>
          </span>
          <span className={styles.fileActions}>
            <button type="button" className={styles.locate} disabled={!file.messageId}
              onClick={() => locate(file.messageId)}>在聊天中定位</button>
            {href
              ? <a className={styles.link} href={href} target="_blank" rel="noreferrer">打开</a>
              : <span className={styles.link} aria-disabled="true">打开</span>}
          </span>
        </article>;
      })}

      {tab === "media" && <div className={styles.grid}>
        {media.map(file => {
          const type = fileKindOf(file.name, file.mediaType);
          const href = hrefFor(file);
          return <figure key={file.id} className={styles.mediaItem}>
            <button type="button" className={styles.mediaButton} title={file.name}
              onClick={() => onOpenFile?.(file)}>
              {type === "image" && href
                ? <img src={href} alt={file.name} loading="lazy" />
                : <span className={styles.video} aria-hidden="true">{type === "video" ? "▶" : fileGlyph(file.name, file.mediaType)}</span>}
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
