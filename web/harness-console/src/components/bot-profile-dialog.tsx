"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import styles from "./bot-edit-dialog.module.css";

/**
 * 助理资料：只有姓名、标题（可选）、描述三项。
 *
 * 权限、计划与工具配置各有自己的入口（侧栏「…」里的编辑弹窗），
 * 不混进资料表单；主助理与子助理走同一套资料逻辑。
 */
export function BotProfileDialog({
  specialistId,
  onClose,
  onSaved,
}: {
  specialistId: string;
  onClose: () => void;
  onSaved: (updated: Specialist) => void;
}) {
  const [detail, setDetail] = useState<Specialist | null>(null);
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    void assistantRequest<{ specialist: Specialist }>(
      `specialists/${encodeURIComponent(specialistId)}`,
    )
      .then(data => {
        if (!alive) return;
        setDetail(data.specialist);
        setName(data.specialist.name);
        setTitle(String((data.specialist.config as { title?: string })?.title ?? ""));
        setDescription(data.specialist.description ?? "");
      })
      .catch(cause => {
        if (alive) setError(cause instanceof Error ? cause.message : "读取失败");
      });
    return () => {
      alive = false;
    };
  }, [specialistId]);

  async function save() {
    if (!detail || busy) return;
    setBusy(true);
    setError("");
    try {
      const updated = await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/profile`,
        "POST",
        {
          name: name.trim() || detail.name,
          title: title.trim(),
          description,
          expectedRevision: detail.revision,
        },
      );
      onSaved(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
      setBusy(false);
    }
  }

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-label="编辑助理资料">
      <div className={styles.panel}>
        <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>
          ×
        </button>
        <label className={styles.field}>
          <span>姓名</span>
          <input value={name} maxLength={80} aria-label="姓名" onChange={event => setName(event.target.value)} />
        </label>
        <label className={styles.field}>
          <span>标题（可选）</span>
          <input
            value={title}
            maxLength={80}
            placeholder="研究、营销、管理"
            aria-label="标题"
            onChange={event => setTitle(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span>描述</span>
          <textarea
            value={description}
            rows={6}
            maxLength={2000}
            aria-label="描述"
            onChange={event => setDescription(event.target.value)}
          />
        </label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button type="button" className={styles.primary} disabled={busy || !detail} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}
