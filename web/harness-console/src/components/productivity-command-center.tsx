"use client";

import { useRouter } from "next/navigation";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  productivityCommandKey,
  productivityCommandResults,
  type ProductivityActionId,
  type ProductivityCommandResult,
} from "../lib/productivity-command-center";
import type { TaskAgent } from "../lib/task-agent-catalog";
import { loadTasks, type TaskSummary } from "../lib/task-history";

const actionDestinations: Partial<Record<ProductivityActionId, string>> = {
  "studio-agents": "/studio/agents",
};

const groupLabels: Record<ProductivityCommandResult["kind"], string> = {
  action: "操作",
  task: "最近任务",
  agent: "可用智能体",
};

function SearchGlyph() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="8.5" cy="8.5" r="5.25" />
      <path d="m12.5 12.5 4 4" />
    </svg>
  );
}

export function ProductivityCommandCenter({
  agents,
  onNewTask,
  onSelectTask,
  onStartWithAgent,
}: {
  agents: readonly TaskAgent[];
  onNewTask: () => void;
  onSelectTask: (task: TaskSummary) => void;
  onStartWithAgent: (agent: TaskAgent) => void;
}) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const results = useMemo(
    () => productivityCommandResults(query, tasks, agents),
    [agents, query, tasks],
  );

  useEffect(() => {
    function onShortcut(event: KeyboardEvent) {
      if (
        !event.repeat &&
        (event.metaKey || event.ctrlKey) &&
        event.key.toLocaleLowerCase() === "k"
      ) {
        event.preventDefault();
        if (open) {
          close();
        } else {
          openCommandCenter();
        }
      }
    }
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setQuery("");
    setActiveIndex(0);
    setLoading(true);
    setError("");
    window.requestAnimationFrame(() => inputRef.current?.focus());
    function keepFocusInside(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ) ?? [],
      ).filter((element) => !element.hidden && element.getAttribute("aria-hidden") !== "true");
      const first = focusable[0];
      const last = focusable.at(-1);
      if (!first || !last) {
        event.preventDefault();
        inputRef.current?.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", keepFocusInside, true);
    void loadTasks()
      .then((nextTasks) => {
        if (active) setTasks(nextTasks);
      })
      .catch((reason) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "最近任务暂不可用");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      document.removeEventListener("keydown", keepFocusInside, true);
    };
  }, [open]);

  useEffect(() => {
    setActiveIndex((current) => Math.min(current, Math.max(0, results.length - 1)));
  }, [results.length]);

  function openCommandCenter() {
    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    setOpen(true);
  }

  function close() {
    setOpen(false);
    setQuery("");
    const previous = previousFocusRef.current;
    window.requestAnimationFrame(() => {
      if (previous?.isConnected) previous.focus();
      else triggerRef.current?.focus();
    });
  }

  function execute(result: ProductivityCommandResult) {
    close();
    if (result.kind === "task") {
      onSelectTask(result.task);
      return;
    }
    if (result.kind === "agent") {
      onStartWithAgent(result.agent);
      return;
    }
    if (result.id === "new-task") {
      onNewTask();
      return;
    }
    const destination = actionDestinations[result.id];
    if (destination) router.push(destination);
  }

  function onInputKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (results.length === 0) return;
      const direction = event.key === "ArrowDown" ? 1 : -1;
      setActiveIndex(
        (current) => (current + direction + results.length) % results.length,
      );
      return;
    }
    if (event.key === "Enter" && results[activeIndex]) {
      event.preventDefault();
      execute(results[activeIndex]);
    }
  }

  let previousKind: ProductivityCommandResult["kind"] | null = null;
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="command-center-trigger"
        onClick={openCommandCenter}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? "productivity-command-center" : undefined}
        title="搜索任务和智能体"
      >
        <SearchGlyph />
        <span>搜索</span>
      </button>
      {open
        ? createPortal(
            <div
              className="command-center-backdrop"
              onMouseDown={(event) => {
                if (event.target === event.currentTarget) close();
              }}
            >
              <section
                ref={dialogRef}
                id="productivity-command-center"
                className="command-center-dialog"
                role="dialog"
                aria-modal="true"
                aria-labelledby="command-center-title"
              >
                <h2 id="command-center-title" className="command-center-title">搜索</h2>
                <label className="command-center-search">
                  <SearchGlyph />
                  <input
                    ref={inputRef}
                    type="search"
                    role="combobox"
                    value={query}
                    placeholder="搜索任务或智能体"
                    aria-label="搜索任务或智能体"
                    aria-controls="command-center-results"
                    aria-expanded="true"
                    aria-activedescendant={
                      results[activeIndex]
                        ? `command-result-${productivityCommandKey(results[activeIndex])}`
                        : undefined
                    }
                    onChange={(event) => {
                      setQuery(event.target.value);
                      setActiveIndex(0);
                    }}
                    onKeyDown={onInputKeyDown}
                  />
                </label>
                <div
                  id="command-center-results"
                  className="command-center-results"
                  role="listbox"
                  aria-label="命令搜索结果"
                >
                  {results.map((result, index) => {
                    const showGroup = result.kind !== previousKind;
                    previousKind = result.kind;
                    return (
                      <div key={productivityCommandKey(result)} className="command-center-result-wrap">
                        {showGroup ? (
                          <p className="command-center-group" aria-hidden="true">
                            {groupLabels[result.kind]}
                          </p>
                        ) : null}
                        <button
                          id={`command-result-${productivityCommandKey(result)}`}
                          type="button"
                          role="option"
                          aria-selected={index === activeIndex}
                          className="command-center-result"
                          data-active={index === activeIndex ? "true" : "false"}
                          onMouseEnter={() => setActiveIndex(index)}
                          onClick={() => execute(result)}
                        >
                          <span className={`command-center-result-mark is-${result.kind}`} aria-hidden="true">
                            {result.kind === "action" ? "→" : result.kind === "task" ? "T" : "A"}
                          </span>
                          <span className="command-center-result-copy">
                            <strong>{result.title}</strong>
                            <small>{result.description}</small>
                          </span>
                          {result.kind === "action" && result.shortcut ? (
                            <kbd>{result.shortcut}</kbd>
                          ) : null}
                        </button>
                      </div>
                    );
                  })}
                  {results.length === 0 ? (
                    <div className="command-center-empty">
                      <strong>没有匹配结果</strong>
                      <span>试试任务标题或智能体名称。</span>
                    </div>
                  ) : null}
                </div>
                <footer className="command-center-footer">
                  <span>{loading ? "正在同步最近任务…" : error || `${results.length} 个可用结果`}</span>
                </footer>
              </section>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}
