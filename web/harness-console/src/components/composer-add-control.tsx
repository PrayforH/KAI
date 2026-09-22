"use client";

import { ComposerPrimitive } from "@assistant-ui/react";
import { useEffect, useId, useRef, useState } from "react";

import { KnowledgeBasePicker, useTaskKnowledge } from "./task-knowledge-context";

/**
 * The composer's single "add" entry point: files and knowledge in one icon.
 *
 * It expands to the side rather than downward, because it carries two sections
 * instead of a menu of commands: the file picker (the assistant-ui attachment
 * primitive, so the upload path is the same one paste uses) and the knowledge
 * picker, whose ticks are the binding — `useTaskKnowledge().toggle` writes the
 * thread's knowledge references directly.
 */
export function ComposerAddControl({
  disabled,
  knowledgeAction,
  hideKnowledge = false,
}: {
  disabled: boolean;
  /**
   * Scope mode (an embedded conversation such as the builder's own thread): the
   * agent owns the knowledge, so the panel offers that configuration surface
   * instead of ticking thread-level bases.
   */
  knowledgeAction?: { label: string; onSelect: () => void };
  /** A compact composer keeps files but drops the knowledge section entirely. */
  hideKnowledge?: boolean;
}) {
  const { selected, available } = useTaskKnowledge();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();

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
  // Other surfaces ask for the knowledge picker by event; that request now opens
  // this panel, since the knowledge selection only lives here.
  useEffect(() => {
    const show = () => {
      if (!disabled) setOpen(true);
    };
    window.addEventListener("harness:select-knowledge", show);
    return () => window.removeEventListener("harness:select-knowledge", show);
  }, [disabled]);

  const count = knowledgeAction ? 0 : selected.length;
  const names = selected
    .map((reference) => available.find((item) => item.reference === reference)?.displayName ?? reference)
    .join("、");

  return (
    <div
      ref={root}
      className="composer-add-control"
      data-active={count > 0 ? "true" : "false"}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="composer-add-trigger"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? panelId : undefined}
        aria-label={count > 0 ? `添加文件或知识库，已选 ${count} 个知识库` : "添加文件或知识库"}
        title={count > 0 ? `已选知识库：${names}` : "添加文件或知识库"}
        onClick={() => setOpen((current) => !current)}
      >
        <svg className="composer-add-icon" viewBox="0 0 20 20" aria-hidden="true">
          <path d="M10 4.5v11M4.5 10h11" />
        </svg>
        {count > 0 ? <span className="composer-add-count" aria-hidden="true">{count}</span> : null}
      </button>
      {open && (
        <section
          id={panelId}
          role="dialog"
          aria-label="添加文件或知识库"
          className="composer-add-panel"
        >
          <div className="composer-add-section">
            <div className="composer-add-section-head">文件</div>
            <ComposerPrimitive.AddAttachment className="composer-add-file">
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4.5v11M4.5 10h11" /></svg>
              <span>添加文件</span>
            </ComposerPrimitive.AddAttachment>
            <p className="composer-add-hint">也可以直接粘贴到输入框；文件作为本轮输入材料。</p>
          </div>
          {!hideKnowledge && (
            <div className="composer-add-section">
              {knowledgeAction ? (
                <button
                  type="button"
                  className="composer-add-action"
                  onClick={() => { setOpen(false); knowledgeAction.onSelect(); }}
                >
                  <span className="task-knowledge-at" aria-hidden="true">@</span>
                  <span>{knowledgeAction.label}</span>
                </button>
              ) : <KnowledgeBasePicker />}
            </div>
          )}
        </section>
      )}
    </div>
  );
}
