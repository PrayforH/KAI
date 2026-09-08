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

type TaskKnowledgeValue = {
  /** Knowledge base references selected for this thread. */
  selected: string[];
  available: StudioKnowledgeBase[];
  loading: boolean;
  toggle: (reference: string) => void;
  clear: () => void;
  isSelected: (reference: string) => boolean;
};

const TaskKnowledgeContext = createContext<TaskKnowledgeValue | null>(null);

export function TaskKnowledgeProvider({
  selected,
  onChange,
  children,
}: {
  selected: string[];
  onChange: (references: string[]) => void;
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

  const value = useMemo<TaskKnowledgeValue>(
    () => ({
      selected,
      available,
      loading,
      toggle,
      clear,
      isSelected: (reference: string) => selected.includes(reference),
    }),
    [available, clear, loading, selected, toggle],
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
export function TaskKnowledgeControl({ disabled }: { disabled: boolean }) {
  const { available, selected, loading, toggle, clear } = useTaskKnowledge();
  const [open, setOpen] = useState(false);
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
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M10 5.2C8.4 4.2 6.3 3.8 3.8 4v11c2.5-.2 4.6.2 6.2 1.2 1.6-1 3.7-1.4 6.2-1.2V4c-2.5-.2-4.6.2-6.2 1.2z" />
          <path d="M10 5.2v11" />
        </svg>
        <span>{label}</span>
      </button>
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
