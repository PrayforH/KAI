"use client";

import { ComposerPrimitive } from "@assistant-ui/react";
import { useEffect, useId, useRef, useState } from "react";
import { KnowledgeBasePicker, useTaskKnowledge } from "./task-knowledge-context";

function PlusIcon() {
  return <svg className="aui-composer-attach-icon" viewBox="0 0 20 20" aria-hidden="true">
    <path d="M10 4.5v11M4.5 10h11" />
  </svg>;
}

export function ComposerAddControl({ filesOnly, knowledgeDisabled, configureKnowledge }: {
  filesOnly: boolean;
  knowledgeDisabled: boolean;
  configureKnowledge?: () => void;
}) {
  const [panel, setPanel] = useState<"add" | "knowledge" | null>(null);
  const { selected } = useTaskKnowledge();
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const visiblePanel = panel === "knowledge" && knowledgeDisabled ? "add" : panel;

  useEffect(() => {
    if (!panel) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setPanel(null);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [panel]);

  if (filesOnly) {
    return <ComposerPrimitive.AddAttachment className="aui-composer-attach" aria-label="添加附件" title="添加附件">
      <PlusIcon />
    </ComposerPrimitive.AddAttachment>;
  }
  const count = configureKnowledge ? 0 : selected.length;
  return <div ref={root} className="composer-add-control" onKeyDown={(event) => {
    if (event.key !== "Escape" || !panel) return;
    event.preventDefault();
    event.stopPropagation();
    setPanel(null);
    trigger.current?.focus();
  }}>
    <button ref={trigger} type="button" className="aui-composer-attach"
      aria-label={count ? `添加附件或知识库，已选 ${count} 个知识库` : "添加附件或知识库"}
      title="添加附件或知识库" aria-haspopup="dialog" aria-expanded={Boolean(visiblePanel)}
      aria-controls={visiblePanel ? panelId : undefined} onClick={() => setPanel(panel ? null : "add")}>
      <PlusIcon />
      {count > 0 && <span className="task-knowledge-count" aria-hidden="true">{count}</span>}
    </button>
    {visiblePanel && <section id={panelId} role="dialog" aria-label={visiblePanel === "knowledge" ? "选择知识库" : "添加附件或知识库"}
      className="task-knowledge-menu composer-add-panel">
      {visiblePanel === "knowledge" ? <KnowledgeBasePicker /> : <>
        <ComposerPrimitive.AddAttachment className="task-knowledge-option" onClick={() => {
          setPanel(null);
          trigger.current?.focus();
        }}>添加附件</ComposerPrimitive.AddAttachment>
        <button type="button" className="task-knowledge-option" disabled={knowledgeDisabled} onClick={() => {
          if (!configureKnowledge) { setPanel("knowledge"); return; }
          setPanel(null);
          configureKnowledge();
        }}>{configureKnowledge ? "配置智能体知识库" : "选择知识库"}</button>
      </>}
    </section>}
  </div>;
}
