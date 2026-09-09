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
const MODE_HINTS: Record<KnowledgeMode, string> = {
  rag: "基于文档切片检索回答",
  wiki: "基于 Wiki 页面（摘要/实体/概念）回答，可点开实体",
};

export function TaskKnowledgeControl({ disabled }: { disabled: boolean }) {
  const { available, selected, loading, toggle, clear, mode, setMode } =
    useTaskKnowledge();
  const [open, setOpen] = useState(false);
  if (loading && available.length === 0) return null;
  // The control shows only the selected count; the names live in the tooltip
  // and the picker itself.
  const count = selected.length;
  const names = selected
    .map((reference) => available.find((item) => item.reference === reference)?.displayName ?? reference)
    .join("、");
  return (
    <div className="task-knowledge-control" data-active={selected.length > 0 ? "true" : "false"}>
      <button
        type="button"
        className="task-knowledge-trigger"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        title={
          count === 0
            ? "选择本次任务使用的知识库，可多选"
            : `已选知识库：${names}`
        }
        onClick={() => setOpen((current) => !current)}
      >
        <span className="task-knowledge-at" aria-hidden="true">
          @
        </span>
        <span className="task-knowledge-count">{count}</span>
      </button>
    </div>
  );
}

/** Compact Wiki on/off switch for the composer toolbar. */
export function TaskKnowledgeModeSwitch({ disabled }: { disabled: boolean }) {
  const { mode, setMode } = useTaskKnowledge();
  const wiki = mode === "wiki";
  return (
    <button
      type="button"
      className={`task-knowledge-switch${wiki ? " is-on" : ""}`}
      disabled={disabled}
      role="switch"
      aria-checked={wiki}
      title={wiki ? "Wiki 问答：基于 Wiki 页面回答" : "RAG 问答：基于文档切片回答"}
      onClick={() => setMode(wiki ? "rag" : "wiki")}
    >
      <span className="task-knowledge-switch-track" aria-hidden="true">
        <span className="task-knowledge-switch-thumb" />
      </span>
      <span className="task-knowledge-switch-label">Wiki</span>
    </button>
  );
}
