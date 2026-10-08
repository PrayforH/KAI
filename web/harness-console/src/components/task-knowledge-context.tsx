"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useId,
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
  error: string;
  retry: () => void;
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
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => setRevision((value) => value + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void studioClient
      .listKnowledgeBases()
      .then((bases) => {
        if (!cancelled) setAvailable(bases);
      })
      .catch(() => {
        if (!cancelled) setError("知识库加载失败，请重试");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [revision]);

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
      error,
      retry,
      toggle,
      clear,
      isSelected: (reference: string) => selected.includes(reference),
    }),
    [available, clear, loading, error, retry, selected, toggle],
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

const TYPE_LABELS: Record<string, string> = { rag: "文档检索", wiki: "Wiki", hybrid: "文档 + Wiki" };

/**
 * The list itself: search, tick, clear. Two surfaces bind different things — a thread
 * and an agent draft — so the list takes them as props and owns no binding of its own.
 */
export function KnowledgeBaseList({
  available,
  selected,
  loading,
  error,
  onToggle,
  onClear,
  onRetry,
  autoFocus = true,
  clearLabel = "清除选择，使用智能体默认知识库",
}: {
  available: StudioKnowledgeBase[];
  selected: readonly string[];
  loading: boolean;
  error: string;
  onToggle: (reference: string) => void;
  onClear: () => void;
  onRetry: () => void;
  autoFocus?: boolean;
  clearLabel?: string;
}) {
  const [query, setQuery] = useState("");
  const matches = available.filter((base) =>
    `${base.displayName} ${base.reference}`.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const count = selected.length;
  return <>
    <div className="task-knowledge-menu-head">知识库 · 已选 {count} 个</div>
    <input autoFocus={autoFocus} className="task-knowledge-search" aria-label="搜索知识库" placeholder="搜索知识库…"
      value={query} onChange={(event) => setQuery(event.target.value)}
      onKeyDown={(event) => { if (event.key === "Enter") event.preventDefault(); }} />
    {loading ? <p className="task-knowledge-empty" role="status">加载中…</p>
      : error ? <div role="alert" className="task-knowledge-empty">{error}<button type="button" onClick={onRetry}>重试</button></div>
      : matches.length ? <ul>{matches.map((base) => (
        <li key={base.reference}><button type="button" className="task-knowledge-option" aria-pressed={selected.includes(base.reference)} onClick={() => onToggle(base.reference)}>
          <span className="task-knowledge-option-mark" aria-hidden="true">{selected.includes(base.reference) ? "✓" : ""}</span>
          <span className="task-knowledge-option-copy"><strong>{base.displayName}</strong><small>{TYPE_LABELS[base.kbType] ?? base.kbType}</small></span>
        </button></li>
      ))}</ul> : <p className="task-knowledge-empty">{query ? "没有匹配的知识库" : "暂无可用知识库"}</p>}
    <button type="button" className="task-knowledge-clear" disabled={!count} onClick={onClear}>{clearLabel}</button>
  </>;
}

/** The same list bound to the thread: what the composer's add panel shows. */
export function KnowledgeBasePicker({ autoFocus = true, clearLabel }: { autoFocus?: boolean; clearLabel?: string }) {
  const { available, selected, loading, error, retry, toggle, clear } = useTaskKnowledge();
  return <KnowledgeBaseList autoFocus={autoFocus} clearLabel={clearLabel} available={available} selected={selected}
    loading={loading} error={error} onToggle={toggle} onClear={clear} onRetry={retry} />;
}


/** Composer toolbar control: shows and edits the thread's knowledge bases. */
export function TaskKnowledgeControl({ disabled, label }: { disabled: boolean; label?: string }) {
  const { selected, available } = useTaskKnowledge();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open]);
  useEffect(() => {
    const show = () => { if (!disabled && !label) { setOpen(true); } };
    window.addEventListener("harness:select-knowledge", show);
    return () => window.removeEventListener("harness:select-knowledge", show);
  }, [disabled, label]);
  // The control shows only the selected count; the names live in the tooltip
  // and the picker itself.
  const count = selected.length;
  const names = selected
    .map((reference) => available.find((item) => item.reference === reference)?.displayName ?? reference)
    .join("、");
  return (
    <div ref={root} className={`task-knowledge-control${label ? " task-knowledge-context-control" : ""}`} data-active={selected.length > 0 ? "true" : "false"}
      onKeyDown={(event) => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
      }}>
      <button
        ref={trigger}
        type="button"
        className={label ? "task-knowledge-context-trigger" : "task-knowledge-trigger"}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? menuId : undefined}
        aria-label={label ? `调整知识库 ${label}` : `选择知识库，已选 ${count} 个`}
        title={
          count === 0
            ? "选择本次任务使用的知识库，可多选"
            : `已选知识库：${names}`
        }
        onClick={() => setOpen((current) => !current)}
      >
        {label ? <>
          <svg className="task-knowledge-folder" viewBox="0 0 20 20" aria-hidden="true"><path d="M2.5 5h5l1.7 2h8.3v10h-15z" /></svg>
          <span className="task-knowledge-selected-name">{label}</span>
          <svg className="task-knowledge-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
        </> : <>
          <span className="task-knowledge-at" aria-hidden="true">@</span>
          {count > 0 ? <span className="task-knowledge-count" aria-hidden="true">{count}</span> : null}
        </>}
      </button>
      {open && (
        <section id={menuId} role="dialog" aria-label="选择知识库" className="task-knowledge-menu"
          ref={(node) => {
            if (!node) return;
            // The menu opens upward from the trigger, but the thread viewport is a
            // scroll box: a menu taller than the space above the trigger gets its
            // top clipped into an unreadable sliver. Cap it at that space instead.
            const top = trigger.current?.getBoundingClientRect().top ?? window.innerHeight;
            const room = Math.round(top - 16);
            node.style.maxHeight = `${Math.max(160, Math.min(360, room))}px`;
          }}>
          <KnowledgeBasePicker />
        </section>
      )}
    </div>
  );
}

export function TaskKnowledgeSelection({ disabled }: { disabled: boolean }) {
  const { selected, available, toggle } = useTaskKnowledge();
  if (!selected.length) return null;
  return <div className="composer-context-shelf task-knowledge-selection" aria-label="本次问答知识库">
    {selected.map((reference) => {
      const name = available.find((item) => item.reference === reference)?.displayName ?? reference;
      return <span className="task-knowledge-selected-chip" key={reference} title={name}>
        <TaskKnowledgeControl disabled={disabled} label={name} />
        <button type="button" disabled={disabled} onClick={() => toggle(reference)} aria-label={`移除知识库 ${name}`}>
          <svg className="task-knowledge-remove" viewBox="0 0 16 16" aria-hidden="true"><path d="m4.6 4.6 6.8 6.8M11.4 4.6 4.6 11.4" /></svg>
        </button>
      </span>;
    })}
  </div>;
}
