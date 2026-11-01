"use client";

import { useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import {
  LOCAL_TOOL_PERMISSIONS,
  grantedLocalTools,
  type LocalToolKey,
} from "../lib/local-tool-permissions";
import styles from "./local-tool-permissions.module.css";

/**
 * 编辑弹窗里的本地工具权限：四项固定能力，逐项授予/收回。
 * 写入走既有 specialists/{id}/config（合并 config + expectedRevision）。
 */
export function LocalToolPermissions({
  specialistId,
  specialist,
  onUpdated,
}: {
  specialistId: string;
  specialist: Specialist;
  onUpdated: (next: Specialist) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const granted = new Set<string>(grantedLocalTools(specialist));

  async function toggle(key: LocalToolKey, allowed: boolean) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const next = LOCAL_TOOL_PERMISSIONS.map((item) => item.key).filter((entry) =>
        entry === key ? allowed : granted.has(entry),
      );
      const updated = await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/config`,
        "POST",
        {
          config: { ...specialist.config, local_tool_permissions: next },
          expectedRevision: specialist.revision,
        },
      );
      onUpdated(updated);
      window.dispatchEvent(
        new CustomEvent("kai:specialists-changed", {
          detail: { specialistId, reason: "local-tools" },
        }),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "权限保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={styles.permissions} aria-label="本地工具权限">
      <p className={styles.title}>本地工具权限</p>
      <p className={styles.hint}>逐项允许这个 Bot 在本机/共享电脑上做什么。</p>
      {LOCAL_TOOL_PERMISSIONS.map((item) => (
        <label className={styles.row} key={item.key}>
          <input
            type="checkbox"
            aria-label={item.label}
            data-tool={item.key}
            checked={granted.has(item.key)}
            disabled={busy}
            onChange={(event) => void toggle(item.key, event.target.checked)}
          />
          <span>{item.label}</span>
        </label>
      ))}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
