"use client";
import {
  useState,
  type ReactNode,
  type KeyboardEvent,
  type FocusEvent,
} from "react";
import type { ThreadMessageLike } from "@assistant-ui/react";
import type { StudioDraft } from "../../lib/agent-studio";
import type { StudioTryRunSummary } from "../../lib/studio-client";
import type { PreviewTurn } from "./agent-preview";
import { AgentPlaygroundThread } from "./agent-playground-thread";
import styles from "./build-workspace.module.css";
export function AgentTestPanel({
  navigation,
  messageOverride,
  afterLastMessage,
  inputSeed,
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
  navigation?: ReactNode;
  messageOverride?: ThreadMessageLike[];
  afterLastMessage?: ReactNode;
  inputSeed?: {key: number; text: string};
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
        <header className={styles.conversationHeader}>
          {navigation}
          <div className={styles.conversationControls}>
            <button type="button" aria-label="查看对话文件" title="文件" onClick={onAssets}><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M3 5h5l2 2h7v9H3z" /></svg></button>
            {!sessionRail && sessions.length > 0 && onSelectSession ? (
              <details
                className={styles.conversationMenu}
                onKeyDown={dismissMenu}
                onBlur={blurMenu}
              >
                <summary
                  aria-label="选择试跑会话"
                  title={turns[0]?.prompt || "新对话"}
                >
                  <span>{turns[0]?.prompt || "新对话"}</span>
                  <span aria-hidden="true">⌄</span>
                </summary>
                <div className={styles.conversationPopover}>
                  <label>
                    历史会话
                    <select
                      aria-label="切换测试对话"
                      value={sessionId}
                      disabled={locked}
                      onChange={(event) => {
                        setSeed(undefined);
                        onSelectSession(event.target.value);
                        event.currentTarget
                          .closest("details")
                          ?.removeAttribute("open");
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
                  {historyError && <p role="alert">{historyError}</p>}
                </div>
              </details>
            ) : (
              <span
                className={styles.conversationTitle}
                title={turns[0]?.prompt}
              >
                {turns[0]?.prompt || "新对话"}
              </span>
            )}
            <button
              aria-label="新对话"
              title="新对话"
              disabled={locked}
              onClick={reset}
            >
              ＋
            </button>
            <details
              className={styles.conversationMenu}
              onKeyDown={dismissMenu}
              onBlur={blurMenu}
            >
              <summary
                className={styles.moreSummary}
                aria-label="对话选项"
                title="对话选项"
              >
                ···
              </summary>
              <div className={styles.conversationPopover}>

                {!!examples.length && (
                  <label>
                    从评测用例开始
                    <select
                      aria-label="选择评测用例"
                      value=""
                      disabled={locked}
                      onChange={(event) => {
                        const sample = examples.find(
                          (item) => item.id === event.target.value,
                        );
                        if (sample)
                          setSeed({ key: Date.now(), text: sample.prompt });
                        event.currentTarget
                          .closest("details")
                          ?.removeAttribute("open");
                      }}
                    >
                      <option value="">选择用例填入输入框</option>
                      {examples.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.id} · {item.prompt.slice(0, 60)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <small>
                  草稿 r{revision}
                  {turns.length ? ` · 当前对话 ${turns.length} 轮` : ""}
                </small>
              </div>
            </details>
          </div>
        </header>
        {(!ready || dirty || (last && last.draftRevision !== revision)) && (
          <div className={styles.revisionNote} role="status">
            {!ready
              ? "先创建智能体，再开始对话"
              : dirty
                ? "发送前会保存配置修改"
                : `配置已更新至 r${revision} · 下一次测试将开启新会话`}
          </div>
        )}
        {!sessionRail && historyError && (
          <p className={styles.revisionNote} role="alert">
            {historyError}
          </p>
        )}
        <AgentPlaygroundThread
          key={`${draftId}:${conversationEpoch}`}
          turns={turns}
          messageOverride={messageOverride}
          afterLastMessage={afterLastMessage}
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
          seed={inputSeed ?? seed}
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

function dismissMenu(event: KeyboardEvent<HTMLDetailsElement>) {
  if (event.key !== "Escape") return;
  event.currentTarget.open = false;
  event.currentTarget.querySelector("summary")?.focus();
}
function blurMenu(event: FocusEvent<HTMLDetailsElement>) {
  if (!event.currentTarget.contains(event.relatedTarget as Node | null))
    event.currentTarget.open = false;
}
