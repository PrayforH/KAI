"use client";

import { useEffect, useRef, useState } from "react";
import { projectClient, type ApiProject } from "../lib/studio-client";
import { ProjectModal } from "./project-modal";
import styles from "./project-reuse.module.css";

type Source = { url: string; title: string; content: string };

export function ProjectWebSearchDialog({ project, onClose, onChanged }: {
  project: ApiProject; onClose: () => void; onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  async function search() {
    if (!query.trim() || saving) return;
    abort.current?.abort();
    const controller = new AbortController(); abort.current = controller;
    setSearching(true); setError(""); setSelected(new Set());
    try {
      const result = await projectClient.searchWeb(project.projectId, query.trim(), controller.signal);
      if (!controller.signal.aborted) { setSources(result.results); setSearched(true); }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "网页搜索失败");
    } finally { if (!controller.signal.aborted) setSearching(false); }
  }

  async function add() {
    if (saving || !selected.size) return;
    setSaving(true); setError("");
    const remaining = new Set(selected);
    try {
      const existing = await projectClient.resources(project.projectId);
      const known = new Set(existing.links.map(item => item.url));
      for (const item of sources.filter(item => selected.has(item.url))) {
        if (!known.has(item.url)) {
          try { await projectClient.addLink(project.projectId, item.url, item.title); }
          catch (cause) {
            const current = await projectClient.resources(project.projectId);
            if (!current.links.some(link => link.url === item.url)) throw cause;
          }
        }
        remaining.delete(item.url);
      }
      onChanged(); onClose();
    } catch (cause) {
      setSelected(remaining); onChanged();
      setError(cause instanceof Error ? cause.message : "添加网页失败");
    } finally { setSaving(false); }
  }

  return <ProjectModal title="搜索网页" onClose={onClose} className={styles.searchDialog}
    toolbar={<form className={styles.searchForm} onSubmit={event => { event.preventDefault(); void search(); }}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="7" /><path d="m16 16 5 5" /></svg>
      <input autoFocus aria-label="搜索网络资源或输入 URL" placeholder="搜索网络资源或输入 URL" maxLength={2048} value={query} onChange={event => setQuery(event.target.value)} />
      <button className={styles.secondary} disabled={!query.trim() || searching || saving}>{searching ? "搜索中…" : "搜索"}</button>
    </form>}
    footer={<><span className={styles.muted}>{selected.size ? `已选择 ${selected.size} 项` : ""}</span><button className={styles.primary} disabled={!selected.size || searching || saving} onClick={() => void add()}>{saving ? "添加中…" : "加入项目"}</button></>}>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {searching ? <p className={styles.muted} role="status">正在搜索…</p> : sources.length ? <ul className={styles.searchResults}>
      {sources.map(item => <li key={item.url}><label>
        <input type="checkbox" aria-label={`选择 ${item.title || item.url}`} checked={selected.has(item.url)} disabled={saving} onChange={event => setSelected(current => { const next = new Set(current); if (event.target.checked) next.add(item.url); else next.delete(item.url); return next; })} />
        <span><strong>{item.title || item.url}</strong><a href={item.url} target="_blank" rel="noreferrer">{item.url}</a>{item.content && <p>{item.content}</p>}</span>
      </label></li>)}
    </ul> : <div className={styles.searchEmpty}>{searched ? <p>没有找到相关网页</p> : <svg viewBox="0 0 48 48" aria-hidden="true"><rect x="4" y="7" width="40" height="32" rx="5" /><path d="M4 16h40M10 11h1m4 0h1" /><circle cx="23" cy="26" r="6" /><path d="m28 31 5 5" /></svg>}</div>}
  </ProjectModal>;
}
