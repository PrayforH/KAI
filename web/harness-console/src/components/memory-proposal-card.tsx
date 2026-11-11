"use client";

import { useState } from "react";
import { memoryClient } from "../lib/memory-client";
import styles from "./memory-proposal-card.module.css";

export type MemoryProposal = {
  entryId: string;
  status: "pending" | "active";
  requiresConfirmation: boolean;
};

/** The propose_memory tool answers with a compact JSON object, wrapped either
 *  as plain text or as MCP content parts depending on the runtime. */
function resultText(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    const joined = value
      .map(item => resultText(item))
      .filter((item): item is string => item !== null)
      .join("");
    return joined || null;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    if (typeof record.text === "string") return record.text;
    if (record.content !== undefined) return resultText(record.content);
    if (typeof record.entryId === "string") return JSON.stringify(record);
  }
  return null;
}

export function parseMemoryProposal(value: unknown): MemoryProposal | null {
  const text = resultText(value);
  if (!text) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const record = parsed as Record<string, unknown>;
  if (typeof record.entryId !== "string" || !record.entryId) return null;
  if (record.status !== "pending" && record.status !== "active") return null;
  return {
    entryId: record.entryId,
    status: record.status,
    requiresConfirmation: record.requiresConfirmation === true || record.status === "pending",
  };
}

/** A policy hook or a truncated model answer can stop the tool before it
 *  reaches the memory bank; say which one so the user is not left guessing. */
export function memoryToolFailure(value: unknown): "untrusted" | "invalid" | null {
  const text = resultText(value);
  if (!text) return null;
  if (text.includes("untrusted-memory-deny")) return "untrusted";
  if (text.includes("InputValidationError")) return "invalid";
  return null;
}

export function MemoryProposalCard({ proposal }: { proposal: MemoryProposal }) {
  const [state, setState] = useState<"idle" | "busy" | "done">(
    proposal.status === "active" ? "done" : "idle",
  );
  const [error, setError] = useState("");

  async function confirm() {
    setState("busy");
    setError("");
    try {
      const entry = (await memoryClient.list()).find(item => item.entryId === proposal.entryId);
      if (!entry) throw new Error("没有找到这条记忆，可能已被处理。");
      if (entry.status !== "active") await memoryClient.confirm(entry);
      setState("done");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "确认失败，请稍后重试。");
      setState("idle");
    }
  }

  if (state === "done") {
    return <section className={styles.card} data-state="done" aria-label="长期记忆">
      <strong className={styles.title}>已写入长期记忆</strong>
      <p className={styles.body}>这条信息已生效，后续对话可以直接检索到。</p>
      <a className={styles.link} href="/settings#memory">管理长期记忆</a>
    </section>;
  }

  return <section className={styles.card} data-state="pending" aria-label="长期记忆待确认">
    <strong className={styles.title}>记忆待确认</strong>
    <p className={styles.body}>
      助理提交了一条长期记忆。敏感策略要求你确认后才会生效，确认前检索不到。
    </p>
    {error && <p className={styles.error} role="alert">{error}</p>}
    <div className={styles.actions}>
      <button type="button" className={styles.primary} disabled={state === "busy"} onClick={() => void confirm()}>
        {state === "busy" ? "正在确认…" : "确认保存"}
      </button>
      <a className={styles.link} href="/settings#memory">去记忆设置查看</a>
    </div>
  </section>;
}

export function MemoryToolNotice({ reason }: { reason: "untrusted" | "invalid" }) {
  const copy = reason === "untrusted"
    ? "这次记忆没有写入：本会话读过网页等外部内容后，平台默认不再接受未经核对的写入。助理提交时需要附上你的原话作出处，网页内容不算数；也可以新开一个会话再让助理记住。"
    : "这次记忆没有送达：助理生成的提交内容不完整。可以让助理再试一次。";
  return <section className={styles.card} data-state="blocked" aria-label="长期记忆未写入">
    <strong className={styles.title}>记忆未写入</strong>
    <p className={styles.body}>{copy}</p>
  </section>;
}
