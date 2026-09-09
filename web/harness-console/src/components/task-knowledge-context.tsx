"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  studioClient,
  type StudioKnowledgeBase,
} from "../lib/studio-client";

export type KnowledgeMode = "rag" | "wiki";

type TaskKnowledgeValue = {
  /** Knowledge base references selected for this thread. */
  selected: string[];
  available: StudioKnowledgeBase[];
  loading: boolean;
  toggle: (reference: string) => void;
  clear: () => void;
  isSelected: (reference: string) => boolean;
  /** Q&A mode: chunk retrieval (RAG) or curated wiki pages. */
  mode: KnowledgeMode;
  setMode: (mode: KnowledgeMode) => void;
};

const TaskKnowledgeContext = createContext<TaskKnowledgeValue | null>(null);

export function TaskKnowledgeProvider({
  selected,
  onChange,
  mode,
  onModeChange,
  children,
}: {
  selected: string[];
  onChange: (references: string[]) => void;
  mode: KnowledgeMode;
  onModeChange: (mode: KnowledgeMode) => void;
  children: ReactNode;
}) {
  const [available, setAvailable] = useState<StudioKnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void studioClient
      .listKnowledgeBases()
      .then((bases) => {
        if (!cancelled) setAvailable(bases);
      })
      .catch(() => {
        if (!cancelled) setAvailable([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = useCallback(
    (reference: string) => {
      onChange(
        selected.includes(reference)
          ? selected.filter((item) => item !== reference)
          : [...selected, reference],
      );
    },
    [onChange, selected],
  );

  const clear = useCallback(() => onChange([]), [onChange]);

  const setMode = useCallback(
    (next: KnowledgeMode) => onModeChange(next),
    [onModeChange],
  );

  const value = useMemo<TaskKnowledgeValue>(
    () => ({
      selected,
      available,
      loading,
      toggle,
      clear,
      isSelected: (reference: string) => selected.includes(reference),
      mode,
      setMode,
    }),
    [available, clear, loading, mode, selected, setMode, toggle],
  );

  return (
    <TaskKnowledgeContext.Provider value={value}>
      {children}
    </TaskKnowledgeContext.Provider>
  );
}

export function useTaskKnowledge(): TaskKnowledgeValue {
  const value = useContext(TaskKnowledgeContext);
  if (value === null) {
    throw new Error("useTaskKnowledge must be used inside TaskKnowledgeProvider");
  }
  return value;
}

/** Composer toolbar control: shows and edits the thread's knowledge bases. */
const MODE_LABELS: Record<KnowledgeMode, string> = {
  rag: "RAG",
  wiki: "Wiki",
};

const MODE_FULL_LABELS: Record<KnowledgeMode, string> = {
  rag: "RAG 问答",
  wiki: "Wiki 问答",
};

const MODE_HINTS: Record<KnowledgeMode, string> = {
  rag: "基于文档切片检索回答",
  wiki: "基于 Wiki 页面（摘要/实体/概念）回答，可点开实体",
};

export function TaskKnowledgeControl({ disabled }: { disabled: boolean }) {
  const { available, selected, loading, toggle, clear, mode, setMode } =
    useTaskKnowledge();
  const [open, setOpen] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);
  if (loading && available.length === 0) return null;
  const label =
    selected.length === 0
      ? "知识库"
      : selected.length === 1
        ? (available.find((item) => item.reference === selected[0])?.displayName ??
          selected[0])
        : `知识库 ${selected.length}`;
  return (
    <div className="task-knowledge-control" data-active={selected.length > 0 ? "true" : "false"}>
      <button
        type="button"
        className="task-knowledge-trigger"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        title="选择本次任务使用的知识库，可多选"
        onClick={() => setOpen((current) => !current)}
      >
        <span className="task-knowledge-at" aria-hidden="true">
          @
        </span>
        <span>{label}</span>
      </button>
      <div className="task-knowledge-mode">
        <button
          type="button"
          className="task-knowledge-mode-trigger"
          disabled={disabled}
          aria-expanded={modeOpen}
          aria-haspopup="menu"
          title={MODE_HINTS[mode]}
          onClick={() => setModeOpen((current) => !current)}
        >
          {MODE_FULL_LABELS[mode]}
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
          </svg>
        </button>
        {modeOpen ? (
          <>
            <button
              type="button"
              className="task-knowledge-backdrop"
              aria-label="关闭问答模式选择"
              onClick={() => setModeOpen(false)}
            />
            <div className="task-knowledge-mode-menu" role="menu">
              {(["rag", "wiki"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={mode === value}
                  className={mode === value ? "is-active" : undefined}
                  onClick={() => {
                    setMode(value);
                    setModeOpen(false);
                  }}
                >
                  <span className="task-knowledge-mode-mark">
                    {mode === value ? "✓" : ""}
                  </span>
                  <span className="task-knowledge-mode-copy">
                    <strong>{MODE_FULL_LABELS[value]}</strong>
                    <small>{MODE_HINTS[value]}</small>
                  </span>
                </button>
              ))}
            </div>
          </>
        ) : null}
      </div>
      {open ? (
        <>
          <button
            type="button"
            className="task-knowledge-backdrop"
            aria-label="关闭知识库选择"
            onClick={() => setOpen(false)}
          />
          <div className="task-knowledge-menu" role="dialog" aria-label="选择知识库">
            <p className="task-knowledge-menu-head">
              选择本次任务使用的知识库（可多选）
            </p>
            {available.length === 0 ? (
              <p className="task-knowledge-empty">还没有可用的知识库</p>
            ) : (
              <ul>
                {available.map((base) => {
                  const active = selected.includes(base.reference);
                  return (
                    <li key={base.reference}>
                      <button
                        type="button"
                        className="task-knowledge-option"
                        aria-pressed={active}
                        onClick={() => toggle(base.reference)}
                      >
                        <span className="task-knowledge-option-mark">
                          {active ? "✓" : ""}
                        </span>
                        <span className="task-knowledge-option-copy">
                          <strong>{base.displayName}</strong>
                          <small>
                            {base.reference} · {base.kbType}
                          </small>
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            {selected.length > 0 ? (
              <button type="button" className="task-knowledge-clear" onClick={clear}>
                清空选择
              </button>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
