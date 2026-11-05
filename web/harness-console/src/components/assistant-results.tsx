"use client";

import { useState } from "react";
import { formatBytes, type ArtifactDetails } from "./artifact-list";
import { RailFilePreview, type PreviewTarget } from "./rail-file-preview";
import styles from "./assistant-results.module.css";

/** Durable artifacts from the conversation, with local preview state only. */
export function AssistantResults({ artifacts }: { artifacts: ArtifactDetails[] }) {
  const [selected, setSelected] = useState<PreviewTarget | null>(null);
  const seen = new Set<string>();
  const files = [...new Map(artifacts.map(file => [file.artifact_id, file])).values()].filter(file => {
    if (!file.name || !file.media_type || !/^[a-f0-9]{64}$/i.test(file.sha256 || "")) return true;
    const key = JSON.stringify([file.run_id, file.thread_id, file.name, file.media_type, file.sha256]);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (!files.length) return null;
  return (
    <section className={styles.results} aria-label="交付文件" data-preview-open={Boolean(selected)}>
      <div className={styles.files}>
        {files.map(file => {
          const name = file.name || "未命名文件";
          const query = file.thread_id ? `?thread_id=${encodeURIComponent(file.thread_id)}` : "";
          const url = `/api/harness/artifacts/${encodeURIComponent(file.artifact_id)}${query}`;
          return (
            <article className={styles.file} key={file.artifact_id}>
              <button type="button" className={styles.open}
                aria-label={`预览 ${name}`}
                aria-expanded={selected?.artifact_id === file.artifact_id}
                onClick={() => setSelected({ ...file, name, media_type: file.media_type || "application/octet-stream" })}>
                <span className={styles.mark} aria-hidden="true">{name.includes(".") ? name.split(".").at(-1)?.slice(0, 5).toUpperCase() : "FILE"}</span>
                <span className={styles.copy}><strong>{name}</strong><small>{formatBytes(file.size_bytes)} · 预览</small></span>
              </button>
              <a className={styles.download} href={url} download={name} aria-label={`下载 ${name}`}>下载</a>
            </article>
          );
        })}
      </div>
      {selected && <div className={styles.preview}>
        <button type="button" className={styles.close} onClick={() => setSelected(null)} aria-label="收起文件预览">收起预览</button>
        <RailFilePreview key={selected.artifact_id} target={selected} />
      </div>}
    </section>
  );
}
