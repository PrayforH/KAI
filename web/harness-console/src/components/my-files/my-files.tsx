"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { requireAuthenticatedResponse } from "../../lib/client-auth";
import styles from "./my-files.module.css";

interface UserFile {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes?: number | null;
  run_id: string;
  thread_id: string;
  thread_title: string;
  agent_name: string;
  created_at: string;
  task_archived: boolean;
}

type FileKind = "all" | "document" | "sheet" | "presentation" | "image" | "other";

const FILTERS: ReadonlyArray<{ id: FileKind; label: string }> = [
  { id: "all", label: "全部" },
  { id: "document", label: "文档" },
  { id: "sheet", label: "表格" },
  { id: "presentation", label: "演示" },
  { id: "image", label: "图片" },
  { id: "other", label: "其他" },
];

function fileKind(file: UserFile): Exclude<FileKind, "all"> {
  const name = file.name.toLocaleLowerCase();
  const media = file.media_type.toLocaleLowerCase();
  if (media.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/.test(name)) return "image";
  if (/spreadsheet|excel|csv/.test(media) || /\.(xlsx?|csv|tsv)$/.test(name)) return "sheet";
  if (/presentation|powerpoint/.test(media) || /\.(pptx?|key)$/.test(name)) return "presentation";
  if (/pdf|word|text|markdown/.test(media) || /\.(pdf|docx?|md|txt)$/.test(name)) return "document";
  return "other";
}

function formatBytes(value?: number | null) {
  if (value === null || value === undefined) return "大小未知";
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(value < 10_240 ? 1 : 0)} KB`;
  return `${(value / 1_048_576).toFixed(value < 10_485_760 ? 1 : 0)} MB`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function MyFiles() {
  const [files, setFiles] = useState<UserFile[]>([]);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<FileKind>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      setLoading(true);
      setError("");
      try {
        const response = requireAuthenticatedResponse(
          await fetch("/api/harness/artifacts?limit=500", {
            cache: "no-store",
            signal: controller.signal,
          }),
        );
        if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
        setFiles(await response.json() as UserFile[]);
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError(cause instanceof Error ? cause.message : "文件索引暂时不可用");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [refreshKey]);

  const counts = useMemo(() => {
    const next = { all: files.length, document: 0, sheet: 0, presentation: 0, image: 0, other: 0 };
    for (const file of files) next[fileKind(file)] += 1;
    return next;
  }, [files]);

  const visibleFiles = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return files.filter((file) => {
      if (kind !== "all" && fileKind(file) !== kind) return false;
      if (!normalized) return true;
      return [file.name, file.thread_title, file.agent_name, file.media_type]
        .join(" ")
        .toLocaleLowerCase()
        .includes(normalized);
    });
  }, [files, kind, query]);

  return (
    <main className={styles.main} id="main-content">
        <header className={styles.hero}>
          <div>
            <p>MY DELIVERABLES</p>
            <h1>我的文件</h1>
            <span>自动汇总任务生成的交付物。文件仍属于原任务，不复制、不跨用户共享。</span>
          </div>
          <div className={styles.summary} aria-label="文件概况">
            <strong>{files.length}</strong>
            <span>个可下载文件</span>
          </div>
        </header>

        <section className={styles.controls} aria-label="筛选文件">
          <label>
            <span className={styles.visuallyHidden}>搜索文件</span>
            <input
              type="search"
              value={query}
              placeholder="搜索文件名、任务或智能体"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className={styles.filters}>
            {FILTERS.map((filter) => (
              <button
                type="button"
                aria-pressed={kind === filter.id}
                onClick={() => setKind(filter.id)}
                key={filter.id}
              >
                <span>{filter.label}</span>
                <small>{counts[filter.id]}</small>
              </button>
            ))}
          </div>
          <button className={styles.refresh} type="button" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading}>
            {loading ? "正在同步" : "刷新"}
          </button>
        </section>

        <section className={styles.filePanel} aria-live="polite">
          <header>
            <span>文件</span>
            <span>来源任务</span>
            <span>生成时间</span>
            <span>操作</span>
          </header>
          {visibleFiles.map((file) => {
            const resolvedKind = fileKind(file);
            return (
              <article className={styles.fileRow} key={file.artifact_id}>
                <div className={styles.fileIdentity}>
                  <i data-kind={resolvedKind}>{file.name.split(".").pop()?.slice(0, 4).toUpperCase() || "FILE"}</i>
                  <div>
                    <strong title={file.name}>{file.name}</strong>
                    <span>{FILTERS.find((filter) => filter.id === resolvedKind)?.label} · {formatBytes(file.size_bytes)}</span>
                  </div>
                </div>
                <div className={styles.source}>
                  <strong title={file.thread_title}>{file.thread_title}</strong>
                  <span>{file.agent_name}{file.task_archived ? " · 任务已归档" : ""}</span>
                </div>
                <time dateTime={file.created_at}>{formatDate(file.created_at)}</time>
                <div className={styles.actions}>
                  <Link href={`/?thread=${encodeURIComponent(file.thread_id)}`}>打开任务</Link>
                  <a href={`/api/harness/artifacts/${encodeURIComponent(file.artifact_id)}`} download>下载</a>
                </div>
              </article>
            );
          })}
          {loading && files.length === 0 && (
            <div className={styles.state}><strong>正在整理你的文件</strong><span>从历史任务中建立可下载交付物索引…</span></div>
          )}
          {!loading && error && (
            <div className={`${styles.state} ${styles.error}`} role="alert"><strong>文件索引暂时不可用</strong><span>{error}</span><button type="button" onClick={() => setRefreshKey((value) => value + 1)}>重新加载</button></div>
          )}
          {!loading && !error && files.length === 0 && (
            <div className={styles.state}><strong>还没有生成文件</strong><span>当任务产出 Word、Excel、PPT、PDF、图片或其他附件后，会自动出现在这里。</span><Link href="/">新建任务</Link></div>
          )}
          {!loading && !error && files.length > 0 && visibleFiles.length === 0 && (
            <div className={styles.state}><strong>没有匹配的文件</strong><span>换一个关键词或文件类型。</span><button type="button" onClick={() => { setQuery(""); setKind("all"); }}>清除筛选</button></div>
          )}
        </section>
    </main>
  );
}
