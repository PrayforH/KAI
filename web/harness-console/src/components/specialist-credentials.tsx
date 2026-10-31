"use client";

import { useState } from "react";
import { saveSpecialistCredential, type Specialist } from "../lib/assistant-client";
import { SecretInput } from "./secret-input";
import styles from "./specialist-credentials.module.css";

export function credentialNames(specialist: Specialist): string[] {
  return Object.keys(specialist.config ?? {})
    .filter((key) => key.startsWith("credential_ref:"))
    .map((key) => key.slice("credential_ref:".length))
    .filter(Boolean)
    .sort();
}

/**
 * 子助理专用凭据：写入走 /specialists/{id}/credentials（密文入库），
 * 页面上只显示「已配置 · 名称」，配置里只有 credential_ref:<名称>，**永不回显密文**。
 */
export function SpecialistCredentials({
  specialistId,
  specialist,
  onUpdated,
  compact = false,
}: {
  specialistId: string;
  specialist: Specialist;
  onUpdated: (next: Specialist) => void;
  compact?: boolean;
}) {
  const [name, setName] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  // 保存成功后立即在列表里出现，不必等父组件重渲染。
  const [savedNames, setSavedNames] = useState<string[]>([]);
  const configured = [...new Set([...credentialNames(specialist), ...savedNames])].sort();

  async function submit() {
    if (busy) return;
    const trimmed = name.trim();
    if (!trimmed || !secret) {
      setError("请填写凭据名称与内容");
      return;
    }
    setBusy(true);
    setError("");
    setSaved("");
    try {
      const updated = await saveSpecialistCredential(specialistId, {
        name: trimmed,
        secret,
        expectedRevision: specialist.revision,
      });
      onUpdated(updated);
      setSaved(trimmed);
      setSavedNames((current) => [...current, trimmed]);
      setName("");
      setSecret("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "凭据保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={compact ? styles.credentialsCompact : styles.credentials} aria-label="子助理凭据">
      <p className={styles.title}>凭据</p>
      {configured.length ? (
        <ul className={styles.list}>
          {configured.map((entry) => (
            <li className={styles.configured} key={entry}>
              已配置 · {entry}
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.empty}>还没有凭据；需要密钥的任务运行前请先配置。</p>
      )}
      <label className={styles.field}>
        <span>凭据名称</span>
        <input
          value={name}
          maxLength={64}
          placeholder="例如 api_key、tushare_token"
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className={styles.field}>
        <span>凭据内容</span>
        <SecretInput
          aria-label="凭据内容"
          revealLabel="凭据内容"
          value={secret}
          autoComplete="new-password"
          placeholder="输入密钥；保存后不再显示"
          onChange={(event) => setSecret(event.target.value)}
        />
      </label>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {saved && !error && (
        <p className={styles.saved} role="status">
          已保存「{saved}」
        </p>
      )}
      <button
        type="button"
        className={styles.submit}
        disabled={busy}
        onClick={() => void submit()}
      >
        {busy ? "保存中…" : "保存凭据"}
      </button>
    </section>
  );
}
