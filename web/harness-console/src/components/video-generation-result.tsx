"use client";
import { CodexResponseIcon } from "./codex-response-icon";

import { useRef, useState } from "react";
import styles from "./video-generation-result.module.css";
import { requireAuthenticatedResponse } from "../lib/client-auth";

export interface VideoResult {
  id: string;
  routeId: string;
  routeLabel: string;
  prompt: string;
  status: "generating" | "succeeded" | "failed" | "cancelled";
  phase?: "queued" | "in_progress";
  progress?: number;
  url?: string;
  downloadUrl?: string;
  error?: string;
  jobId?: string;
  cancellationSupported?: boolean;
  elapsedSeconds?: number;
  settings: { seconds: number };
}

function Icon({ name }: { name: "open" | "download" | "retry" | "reuse" | "stop" }) {
  if (name === "download") return <CodexResponseIcon name="download" />;
  return <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === "open" ? <><path d="M11 3.5h5.5V9M16 4l-7 7" /><path d="M8 4H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-3" /></> : null}
    {name === "retry" ? <><path d="M4.5 7a6 6 0 1 1-.2 5M4 3.5V8h4.5" /></> : null}
    {name === "reuse" ? <><rect x="6.5" y="6.5" width="10" height="10" rx="2" /><path d="M13.5 6.5V5a1.5 1.5 0 0 0-1.5-1.5H5A1.5 1.5 0 0 0 3.5 5v7A1.5 1.5 0 0 0 5 13.5h1.5" /></> : null}
    {name === "stop" ? <rect x="5" y="5" width="10" height="10" rx="2" /> : null}
  </svg>;
}

export function VideoGenerationResult({ entry, busy = false, onRetry, onCancel, onReuse }: {
  entry: VideoResult;
  busy?: boolean;
  onRetry: () => void;
  onCancel: () => void;
  onReuse: () => void;
}) {
  const player = useRef<HTMLVideoElement>(null);
  const [playbackError, setPlaybackError] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const waiting = entry.status === "generating";
  const stoppedWaiting = entry.status === "cancelled" && entry.cancellationSupported === false;
  const canResume = Boolean(entry.jobId) && (entry.status === "failed" || stoppedWaiting);
  const retryLabel = canResume ? "继续查看" : entry.status === "failed" ? "重试" : "重新生成";

  async function download() {
    if (!entry.url || downloading) return;
    setDownloading(true);
    setDownloadError("");
    try {
      // Playback keeps the provider URL. Fetch bytes only on an explicit
      // click; the authenticated KAI endpoint handles CDN CORS restrictions.
      const response = requireAuthenticatedResponse(await fetch(entry.downloadUrl ?? entry.url));
      if (!response.ok) throw new Error();
      const blob = await response.blob();
      if (!blob.type.startsWith("video/")) throw new Error();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${entry.routeId}.mp4`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch {
      setDownloadError("下载暂不可用，可打开视频链接另存为。");
    } finally { setDownloading(false); }
  }

  return <article className={styles.response} data-status={entry.status} aria-label="视频生成结果">
    {waiting ? <div className={styles.activity} role="status" aria-live="polite">
      <span className={styles.dot} aria-hidden="true" />
      <span>{entry.phase === "queued" ? "视频排队中" : "正在生成视频"}</span>
      {typeof entry.progress === "number" && entry.progress > 0 ? <span>{entry.progress}%</span> : null}
      <button className={styles.action} type="button" title={entry.cancellationSupported === false ? "停止等待" : "取消任务"} aria-label={entry.cancellationSupported === false ? "停止等待" : "取消任务"} onClick={onCancel}><Icon name="stop" /></button>
    </div> : null}
    {entry.status === "succeeded" && entry.url ? <>
      <p className={styles.answer}>视频已生成。</p>
      <video key={entry.url} ref={player} className={styles.player} src={entry.url} controls playsInline preload="metadata" aria-label={entry.prompt} onError={() => setPlaybackError(true)} onLoadedData={() => setPlaybackError(false)} />
      {playbackError ? <p className={styles.error} role="alert">视频暂时无法播放，链接可能已过期。可尝试打开原链接。</p> : null}
    </> : null}
    {entry.error ? <p className={styles.error} role={entry.status === "failed" ? "alert" : "status"}>{entry.error}</p> : null}
    {!waiting ? <div className={styles.footer}>
      <span className={styles.metadata}>{entry.routeLabel} · {entry.settings.seconds} 秒{entry.elapsedSeconds ? ` · ${Math.round(entry.elapsedSeconds)} 秒完成` : ""}</span>
      <nav className={styles.actions} aria-label="视频操作">
        {entry.url ? <>
          <a className={styles.action} href={entry.url} target="_blank" rel="noopener noreferrer" title="打开视频" aria-label="打开视频"><Icon name="open" /></a>
          <button className={styles.action} type="button" disabled={downloading} onClick={() => void download()} title="下载 MP4" aria-label={downloading ? "正在下载" : "下载 MP4"}><Icon name="download" /></button>
        </> : null}
        <button className={styles.action} type="button" onClick={onReuse} title="沿用参数" aria-label="沿用参数"><Icon name="reuse" /></button>
        <button className={styles.action} type="button" disabled={busy} onClick={onRetry} title={retryLabel} aria-label={retryLabel}><Icon name="retry" /></button>
      </nav>
    </div> : null}
    {downloadError ? <p className={styles.error} role="alert">{downloadError}</p> : null}
  </article>;
}
