"use client";
import { useState } from "react";
import type { StudioDraft } from "../../lib/agent-studio";
import type { StudioTryRunSummary } from "../../lib/studio-client";
import type { PreviewTurn } from "./agent-preview";
import { AgentPlaygroundThread } from "./agent-playground-thread";
import styles from "./build-workspace.module.css";
export function AgentTestPanel({
  sessionRail = false,
  savedRuns = [],
  historyLoading = false,
  historyError = "",
  examples = [],
  history = [],
  sessionId = "",
  conversationEpoch = 0,
  onSelectSession,
  incomingFiles,
  onIncomingFilesUsed,
  turns,
  draft,
  draftId,
  revision,
  agentName,
  model,
  busy,
  ready,
  dirty,
  error,
  selectedRunId,
  onSend,
  onRerun,
  onReset,
  onCancel,
  onImprove,
  onAssets,
  onConfigureKnowledge = onAssets,
  userId = "playground",
}: {
  sessionRail?: boolean;
  savedRuns?: StudioTryRunSummary[];
  historyLoading?: boolean;
  historyError?: string;
  examples?: { id: string; prompt: string }[];
  history?: PreviewTurn[];
  sessionId?: string;
  conversationEpoch?: number;
  onSelectSession?: (id: string) => void;
  incomingFiles?: {
    draftId: string;
    files: { id: string; name: string }[];
  } | null;
  onIncomingFilesUsed?: () => void;
  turns: PreviewTurn[];
  draft?: StudioDraft;
  draftId: string;
  revision: number;
  agentName: string;
  model: string;
  busy: boolean;
  ready: boolean;
  dirty: boolean;
  error: string;
  selectedRunId: string;
  userId?: string;
  onSend: (value: string, ids: string[], names: string[]) => Promise<boolean>;
  onRerun?: (value: string, ids: string[], names: string[]) => Promise<boolean>;
  onReset: () => void;
  onCancel: () => Promise<void>;
  onImprove: (turn: PreviewTurn) => void;
  onAssets: () => void;
  onConfigureKnowledge?: () => void;
}) {
  const [search, setSearch] = useState("");
  const [seed, setSeed] = useState<{ key: number; text: string }>();
  const summaries = [
    ...savedRuns.map((item) => ({
      id: item.run.session_id,
      prompt: item.run.input.prompt || "历史任务",
      revision: item.draftRevision,
      date: item.run.created_at,
    })),
    ...history.map((turn) => ({
      id: turn.result.run.session_id,
      prompt: turn.prompt,
      revision: turn.result.draftRevision,
      date: "",
    })),
  ];
  const sessions = [
    ...new Map(summaries.map((item) => [item.id, item])).values(),
  ];
  const last = turns.at(-1)?.result;
  const locked = busy || historyLoading;
  const reset = () => {
    setSeed(undefined);
    onReset();
  };
  return (
    <div className={styles.testLayout} data-session-rail={sessionRail}>
      {sessionRail && (
        <aside className={styles.sessionRail} aria-label="历史会话">
          <header>
            <strong>Sessions</strong>
            <button aria-label="新会话" disabled={locked} onClick={reset}>
              ＋
            </button>
          </header>
          <input
            type="search"
            placeholder="搜索会话"
            aria-label="搜索会话"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className={styles.sessionList}>
            {sessions
              .filter((item) =>
                item.prompt.toLowerCase().includes(search.toLowerCase()),
              )
              .map((item) => (
                <button
                  key={item.id}
                  aria-pressed={sessionId === item.id}
                  disabled={locked}
                  onClick={() => {
                    setSeed(undefined);
                    onSelectSession?.(item.id);
                  }}
                >
                  <strong>{item.prompt}</strong>
                  <small>
                    r{item.revision}
                    {item.date
                      ? ` · ${new Date(item.date).toLocaleDateString()}`
                      : ""}
                  </small>
                </button>
              ))}
            <p>最近 100 个会话，最多 200 次运行</p>
            {historyLoading && <p role="status">读取会话中…</p>}
            {historyError && <p role="alert">{historyError}</p>}
            {!sessions.length && !historyLoading && !historyError && (
              <p>尚无会话。开始试运行后，会话会保存在这里。</p>
            )}
          </div>
        </aside>
      )}
      <section className={styles.testPanel} aria-label="智能体效果测试">
        <header className={styles.panelHeader}>
          <div>
            <strong>Chat</strong>
            <small>{model || "智能体默认模型"}</small>
          </div>
          <div className={styles.headerActions}>
            <button onClick={onAssets}>文件</button>
            <button disabled={locked} onClick={reset}>
              新对话
            </button>
          </div>
        </header>
        {!sessionRail && sessions.length > 0 && onSelectSession && (
          <label className={styles.sessionPicker}>
            测试对话
            <select
              aria-label="切换测试对话"
              value={sessionId}
              disabled={locked}
              onChange={(event) => {
                setSeed(undefined);
                onSelectSession(event.target.value);
              }}
            >
              <option value="">新对话</option>
              {sessions.map((item, index) => (
                <option key={item.id} value={item.id}>
                  对话 {index + 1} · {item.prompt.slice(0, 36)}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className={styles.revisionNote} role="status">
          {!ready
            ? "先创建智能体，再开始对话"
            : dirty
              ? "发送前会保存配置修改"
              : last && last.draftRevision !== revision
                ? `配置已更新至 r${revision} · 下一次测试将开启新会话`
                : `草稿 r${revision} · ${turns.length ? `当前对话 ${turns.length} 轮` : "新对话"}`}
        </div>
        {!!examples.length && (
          <label className={styles.sessionPicker}>
            从评测用例开始
            <select
              value=""
              disabled={locked}
              onChange={(event) => {
                const sample = examples.find(
                  (item) => item.id === event.target.value,
                );
                if (sample) setSeed({ key: Date.now(), text: sample.prompt });
              }}
            >
              <option value="">选择一个用例填入输入框</option>
              {examples.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id} · {item.prompt.slice(0, 60)}
                </option>
              ))}
            </select>
          </label>
        )}
        <AgentPlaygroundThread
          key={`${draftId}:${conversationEpoch}`}
          turns={turns}
          draft={draft}
          agentName={agentName}
          model={model}
          draftId={draftId}
          scopeId={`preview:${draftId}:${sessionId || "new"}:${conversationEpoch}`}
          userId={userId}
          ready={ready}
          busy={busy}
          loading={historyLoading}
          error={error}
          seed={seed}
          selectedRunId={selectedRunId}
          onSend={onSend}
          onRerun={onRerun}
          onCancel={onCancel}
          onReset={reset}
          onImprove={onImprove}
          onAssets={onAssets}
          onConfigureKnowledge={onConfigureKnowledge}
          incomingFiles={incomingFiles}
          onIncomingFilesUsed={onIncomingFilesUsed}
        />
      </section>
    </div>
  );
}
