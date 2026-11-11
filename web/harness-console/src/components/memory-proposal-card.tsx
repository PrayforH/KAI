"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { memoryClient, type MemoryEntry } from "../lib/memory-client";
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

type Phase =
  | "loading"
  | "pending"
  | "confirming"
  | "rejecting"
  | "done"
  | "rejected"
  | "not-found"
  | "error";

type FailedAction = "load" | "confirm" | "reject";

type Tone = "muted" | "warning" | "accent" | "success" | "danger";

/** About four lines of body copy. A longer entry gets an explicit toggle; the
 *  character count also keeps the toggle available before layout measurement
 *  (and in environments where the card is measured with zero height). */
const SUMMARY_CHARS = 120;

const BADGE: Record<Phase, { label: string; tone: Tone }> = {
  loading: { label: "读取中", tone: "muted" },
  pending: { label: "待确认", tone: "warning" },
  confirming: { label: "正在保存", tone: "accent" },
  rejecting: { label: "正在拒绝", tone: "accent" },
  done: { label: "已生效", tone: "success" },
  rejected: { label: "已拒绝", tone: "muted" },
  "not-found": { label: "未写入", tone: "warning" },
  error: { label: "读取失败", tone: "danger" },
};

const SENSITIVITY_COPY: Record<MemoryEntry["sensitivity"], string> = {
  personal: "个人记忆",
  sensitive: "敏感内容",
  prohibited: "禁止写入",
};

function describeSource(source: MemoryEntry["source"]): string {
  const kind = source.kind === "user" ? "你提供" : source.kind === "agent" ? "助理整理" : "导入内容";
  const label = source.label?.trim();
  return label && label !== kind ? `${kind} · ${label}` : kind;
}

function describeScope(entry: MemoryEntry): string {
  const agent = entry.agentName || "当前助理";
  return entry.projectId ? `${agent} · 项目 ${entry.projectId}` : `${agent} · 全部对话`;
}

function errorMessage(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

export function MemoryProposalCard({ proposal }: { proposal: MemoryProposal }) {
  const [phase, setPhase] = useState<Phase>(proposal.status === "active" ? "done" : "loading");
  const [entry, setEntry] = useState<MemoryEntry | null>(null);
  const [error, setError] = useState("");
  const [failedAction, setFailedAction] = useState<FailedAction | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const contentRef = useRef<HTMLParagraphElement>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  /** The tool result only carries the entry id. Read the entry the same way
   *  confirm does, so the card can show exactly what is being approved. */
  const load = useCallback(async () => {
    if (proposal.status === "active") {
      setPhase("done");
      return;
    }
    setPhase("loading");
    setError("");
    setFailedAction(null);
    try {
      const found = (await memoryClient.list()).find(item => item.entryId === proposal.entryId) ?? null;
      if (!mountedRef.current) return;
      if (!found) {
        setEntry(null);
        setPhase("not-found");
        return;
      }
      if (found.status === "active") {
        setEntry(found);
        setPhase("done");
        return;
      }
      if (found.status === "rejected") {
        setEntry(found);
        setPhase("rejected");
        return;
      }
      if (found.status !== "pending") {
        setEntry(null);
        setPhase("not-found");
        return;
      }
      setEntry(found);
      setPhase("pending");
    } catch (cause) {
      if (!mountedRef.current) return;
      setError(errorMessage(cause, "记忆内容读取失败，请稍后重试。"));
      setFailedAction("load");
      setPhase("error");
    }
  }, [proposal.entryId, proposal.status]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (entry && entry.content.length > SUMMARY_CHARS) setOverflowing(true);
    const node = contentRef.current;
    if (node && node.scrollHeight > node.clientHeight + 1) setOverflowing(true);
  }, [entry, phase]);

  async function resolve(action: "confirm" | "reject") {
    if (!entry || phase === "confirming" || phase === "rejecting") return;
    setPhase(action === "confirm" ? "confirming" : "rejecting");
    setError("");
    setFailedAction(null);
    try {
      if (action === "confirm") {
        if (entry.status !== "active") await memoryClient.confirm(entry);
        if (!mountedRef.current) return;
        setPhase("done");
      } else {
        await memoryClient.reject(entry);
        if (!mountedRef.current) return;
        setPhase("rejected");
      }
    } catch (cause) {
      if (!mountedRef.current) return;
      setError(errorMessage(
        cause,
        action === "confirm" ? "保存失败，请稍后重试。" : "拒绝失败，请稍后重试。",
      ));
      setFailedAction(action);
      setPhase("error");
    }
  }

  const isBusy = phase === "confirming" || phase === "rejecting";
  const badge = phase === "error"
    ? {
        label: failedAction === "confirm" ? "保存失败" : failedAction === "reject" ? "拒绝失败" : "读取失败",
        tone: "danger" as const,
      }
    : BADGE[phase];
  const showEntry = entry !== null && (phase === "pending" || isBusy || (phase === "error" && failedAction !== "load"));

  return (
    <section
      className={styles.card}
      data-state={phase}
      aria-label="长期记忆提案"
      aria-busy={isBusy}
    >
      <div className={styles.header}>
        <span className={styles.badge} data-tone={badge.tone} role="status" aria-live="polite">
          {badge.label}
        </span>
        <span className={styles.kicker}>长期记忆</span>
      </div>

      {phase === "loading" && (
        <p className={styles.lead}>正在读取这条记忆的内容…</p>
      )}

      {showEntry && entry && (
        <div className={styles.entry}>
          <p
            ref={contentRef}
            className={styles.content}
            data-expanded={expanded || !overflowing}
          >
            {entry.content}
          </p>
          {overflowing && (
            <button
              type="button"
              className={styles.expand}
              aria-expanded={expanded}
              onClick={() => setExpanded(value => !value)}
            >
              {expanded ? "收起" : "展开全文"}
            </button>
          )}
          <dl className={styles.meta}>
            <div>
              <dt>来源</dt>
              <dd>{describeSource(entry.source)}</dd>
            </div>
            <div>
              <dt>敏感度</dt>
              <dd>
                <span className={styles.sensitivity} data-level={entry.sensitivity}>
                  {SENSITIVITY_COPY[entry.sensitivity]}
                </span>
              </dd>
            </div>
            <div>
              <dt>生效范围</dt>
              <dd>{describeScope(entry)}</dd>
            </div>
          </dl>
        </div>
      )}

      {phase === "pending" && (
        <p className={styles.hint}>
          敏感策略要求你确认后才会生效，确认前检索不到。
        </p>
      )}

      {phase === "done" && (
        <>
          <strong className={styles.stateTitle}>已写入长期记忆</strong>
          <p className={styles.hint}>这条信息已生效，后续对话可以直接检索到。</p>
        </>
      )}

      {phase === "rejected" && (
        <>
          <strong className={styles.stateTitle}>已拒绝这条记忆</strong>
          <p className={styles.hint}>没有写入长期记忆，后续对话不会检索到。助理可以重新提议。</p>
        </>
      )}

      {(phase === "not-found" || (phase === "error" && !entry)) && (
        <>
          <strong className={styles.stateTitle}>内容暂时取不到</strong>
          <p className={styles.hint}>这条记忆可能已被处理或删除，去记忆设置查看当前状态。</p>
        </>
      )}

      {error && (
        <p className={styles.alert} role="alert">
          {failedAction === "confirm" ? `保存失败：${error}`
            : failedAction === "reject" ? `拒绝失败：${error}`
              : error}
        </p>
      )}

      {entry && (phase === "pending" || isBusy) && (
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.confirm}
            disabled={isBusy}
            aria-busy={phase === "confirming"}
            onClick={() => void resolve("confirm")}
          >
            {phase === "confirming"
              ? <><span className={styles.spinner} aria-hidden="true" />正在保存…</>
              : "确认保存"}
          </button>
          <button
            type="button"
            className={styles.reject}
            disabled={isBusy}
            aria-busy={phase === "rejecting"}
            onClick={() => void resolve("reject")}
          >
            {phase === "rejecting"
              ? <><span className={styles.spinner} aria-hidden="true" />正在拒绝…</>
              : "不保存"}
          </button>
        </div>
      )}

      {phase === "error" && entry && (
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.confirm}
            onClick={() => void resolve(failedAction === "reject" ? "reject" : "confirm")}
          >
            {failedAction === "reject" ? "重试拒绝" : "重试保存"}
          </button>
          <button
            type="button"
            className={styles.reject}
            onClick={() => void resolve(failedAction === "reject" ? "confirm" : "reject")}
          >
            {failedAction === "reject" ? "确认保存" : "不保存"}
          </button>
        </div>
      )}

      {phase === "error" && !entry && (
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={() => void load()}>
            重试读取
          </button>
          <a className={styles.link} href="/settings#memory">去记忆设置查看</a>
        </div>
      )}

      {phase === "not-found" && (
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={() => void load()}>
            重试读取
          </button>
          <a className={styles.link} href="/settings#memory">去记忆设置查看</a>
        </div>
      )}

      {(phase === "pending" || isBusy) && (
        <div className={styles.footer}>
          <a className={styles.link} href="/settings#memory">去记忆设置查看</a>
        </div>
      )}

      {(phase === "done" || phase === "rejected") && (
        <div className={styles.footer}>
          <a className={styles.link} href="/settings#memory">
            {phase === "done" ? "管理长期记忆" : "去记忆设置查看"}
          </a>
        </div>
      )}
    </section>
  );
}

export function MemoryToolNotice({ reason }: { reason: "untrusted" | "invalid" }) {
  const copy = reason === "untrusted"
    ? "这次记忆没有写入：本会话读过网页等外部内容后，平台默认不再接受未经核对的写入。助理提交时需要附上你的原话作出处，网页内容不算数；也可以新开一个会话再让助理记住。"
    : "这次记忆没有送达：助理生成的提交内容不完整。可以让助理再试一次。";
  return <section className={styles.card} data-state="blocked" aria-label="长期记忆未写入">
    <div className={styles.header}>
      <span className={styles.badge} data-tone="danger">未写入</span>
      <span className={styles.kicker}>长期记忆</span>
    </div>
    <strong className={styles.stateTitle}>记忆未写入</strong>
    <p className={styles.hint}>{copy}</p>
  </section>;
}
