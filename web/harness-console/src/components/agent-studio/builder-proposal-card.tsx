"use client";

import { useState } from "react";
import type { StudioBuilderChanges } from "../../lib/studio-client";
import styles from "./builder-proposal-card.module.css";

const fieldNames: Record<string, string> = {
  goal: "目标", audience: "面向用户", inputs: "输入", outputs: "输出", constraints: "约束", examples: "示例",
  name: "名称", description: "说明", instructions: "指令", alias: "角色", responsibility: "职责",
  packageId: "技能", revision: "版本", path: "文件路径", content: "文件内容",
};
const protectedFields = new Set(["source", "contentBase64", "contentSha256", "fileCount", "filesTruncated", "sizeBytes", "binary", "retained"]);

// Preserve the proposal's shape and metadata. Only edit user-facing values;
// catalog revisions and bundled file provenance remain server-validated.
function ValueEditor({ value, label, disabled, onChange }: {
  value: unknown; label: string; disabled: boolean; onChange: (value: unknown) => void;
}) {
  if (typeof value === "string") return <label className={styles.field}><span>{label}</span>
    <textarea aria-label={label} value={value} disabled={disabled} rows={value.includes("\n") || value.length > 80 ? 5 : 2} onChange={event => onChange(event.target.value)} />
  </label>;
  if (Array.isArray(value)) return <div className={styles.fields}>
    {value.map((item, index) => <div key={index} className={styles.listItem}>
      <ValueEditor value={item} label={`${label} ${index + 1}`} disabled={disabled} onChange={next => onChange(value.map((old, i) => i === index ? next : old))} />
    </div>)}
    {!value.length && <span className={styles.muted}>清空此项</span>}
  </div>;
  if (value && typeof value === "object") return <div className={styles.fields}>
    {Object.entries(value).filter(([key]) => !protectedFields.has(key)).map(([key, item]) =>
      <ValueEditor key={key} value={item} label={`${label} · ${fieldNames[key] ?? key}`} disabled={disabled} onChange={next => onChange({...value, [key]: next})} />)}
  </div>;
  return <div className={styles.muted}>{label}：{String(value ?? "未设置")}</div>;
}

export function BuilderProposalCard({ changes, selected, labels, before, revision, disabled, stale, comparing, canRerun, rerunDisabled, onChange, onSelect, onPreview, onApply, onDiscard }: {
  changes: StudioBuilderChanges;
  selected: string[];
  labels: Record<string, string>;
  before: (key: string) => unknown;
  revision: number;
  disabled: boolean;
  stale: boolean;
  comparing: boolean;
  canRerun: boolean;
  rerunDisabled: boolean;
  onChange: (changes: StudioBuilderChanges) => void;
  onSelect: (keys: string[]) => void;
  onPreview?: () => void;
  onApply: (rerun: boolean) => void;
  onDiscard: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const fields = Object.entries(changes).filter(([key]) => key !== "capabilityCatalogRevision");
  const blocked = disabled || stale || !selected.length;
  return <section className={styles.card} aria-label="待确认的配置修改">
    <header className={styles.header}>
      <button type="button" className={styles.heading} aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>
        <svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d={collapsed ? "m6 4 4 4-4 4" : "m4 6 4 4 4-4"} /></svg>
        <strong>修改预览</strong><span>{selected.length}/{fields.length} 项</span>
      </button>
      {onPreview && <button type="button" disabled={blocked} onClick={onPreview}>{comparing ? "生成差异…" : "查看代码差异 ↗"}</button>}
    </header>
    {!collapsed && <div className={styles.body}>
      {fields.map(([key, value]) => <div key={key} className={styles.change}>
        <div className={styles.row}>
          <label><input type="checkbox" aria-label={`选择${labels[key] ?? key}`} checked={selected.includes(key)} disabled={disabled || stale} onChange={event => onSelect(event.target.checked ? [...selected, key] : selected.filter(item => item !== key))} /><span>{labels[key] ?? key}</span></label>
          <button type="button" aria-label={`编辑${labels[key] ?? key}`} aria-expanded={expanded === key} onClick={() => setExpanded(expanded === key ? null : key)}>{expanded === key ? "收起" : "编辑"}</button>
        </div>
        {expanded === key && <div className={styles.editor}>
          <ValueEditor value={value} label={labels[key] ?? key} disabled={disabled || stale || !selected.includes(key)} onChange={next => onChange({...changes, [key]: next})} />
          <details><summary>修改前</summary><pre>{typeof before(key) === "string" ? before(key) as string : JSON.stringify(before(key), null, 2)}</pre></details>
        </div>}
      </div>)}
    </div>}
    {stale && <p className={styles.warning} role="alert">配置已有新变化，请保存后重新生成建议。</p>}
    <footer className={styles.actions}>
      <span>草稿 r{revision}</span>
      <button type="button" disabled={disabled} onClick={onDiscard}>放弃建议</button>
      {canRerun && <button type="button" disabled={blocked || rerunDisabled} onClick={() => onApply(true)}>应用并重新试跑</button>}
      <button type="button" className={styles.apply} disabled={blocked} onClick={() => onApply(false)}>应用修改</button>
    </footer>
  </section>;
}
