"use client";
import { useState } from "react";
import type { PersonalAgentVersion } from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import type { BuildChange } from "./agent-build-assets";
import { ProjectSourceDiff } from "./project-source-diff";
import styles from "./agent-studio.module.css";

export function AgentVersionHistory({draft, changes, versions, loading, error, onRetry, canPublish, promoting, confirmVersion, onConfirm, onPromote}: {
  draft: StudioDraft; changes: BuildChange[]; versions: PersonalAgentVersion[]; loading: boolean; error: string;
  onRetry: () => void; canPublish: boolean; promoting: string; confirmVersion: string;
  onConfirm: (version: string) => void; onPromote: (version: string) => void;
}) {
  const [selected, setSelected] = useState("draft");
  const version = versions.find(version => version.version === selected);
  return <div className={styles.historyLayout}>
    <nav className={styles.historyVersions} aria-label="选择智能体版本">
      <button type="button" aria-current={selected === "draft" ? "true" : undefined} onClick={() => setSelected("draft")}><strong>草稿 r{draft.revision}</strong><small>{changes.length ? `${changes.length} 项最近修改` : "当前配置"}</small></button>
      {versions.map(item => <button type="button" key={item.version} aria-current={selected === item.version ? "true" : undefined} onClick={() => setSelected(item.version)}><strong>{item.version}{item.version === item.current_version && <em>当前</em>}</strong><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></button>)}
      {loading && <p role="status">正在读取版本…</p>}
      {error && <p role="alert">{error}<button onClick={onRetry}>重试</button></p>}
      {!loading && !error && !versions.length && <p>尚无发布版本</p>}
    </nav>
    <section className={styles.historyDetail} aria-label="版本修改详情">
      {version ? <><h3>{version.version}</h3><p>发布于 {new Date(version.created_at).toLocaleString("zh-CN")}</p><p>该历史版本暂未提供文件快照，无法展示逐行差异。</p><dl><dt>内容校验</dt><dd>{version.manifest_hash}</dd><dt>包校验</dt><dd>{version.package_hash || "未记录"}</dd></dl>
        {version.version === version.current_version ? <p>新任务正在使用此版本。</p> : <div className={styles.historyRestore}>{confirmVersion === version.version ? <><p>切换只影响新任务，已有任务保持原版本。</p><button disabled={Boolean(promoting)} onClick={() => onPromote(version.version)}>{promoting ? "切换中…" : "确认切换"}</button><button onClick={() => onConfirm("")}>取消</button></> : <button disabled={!canPublish || Boolean(promoting)} onClick={() => onConfirm(version.version)}>设为当前版本</button>}</div>}
      </> : <><h3>草稿 r{draft.revision} 的修改</h3><p>本次打开期间的最近改动</p>{changes.length ? changes.map((change,index) => <details key={index} open><summary>{change.label === "系统提示词" ? "AGENTS.md · 指令" : change.label}</summary><ProjectSourceDiff theme="light" wrap change={{path:change.label === "系统提示词" ? "AGENTS.md" : `${change.label}.txt`,status:"modified",before:{path:change.label,content:change.before,size:change.before.length,unavailable:null},after:{path:change.label,content:change.after,size:change.after.length,unavailable:null}}} /></details>) : <p>本次打开后尚无已应用的配置修改。</p>}</>}
    </section>
  </div>;
}
