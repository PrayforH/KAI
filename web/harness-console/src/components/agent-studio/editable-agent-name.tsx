"use client";

import { useCallback, useRef, useState } from "react";
import styles from "./agent-studio.module.css";

/** Edits the display name through the same draft update/save flow as configuration. */
export function EditableAgentName({ name, disabled, onChange }: {
  name: string; disabled: boolean; onChange: (name: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);
  const composing = useRef(false);
  const endedAt = useRef(-Infinity);
  const finished = useRef(false);
  const focusInput = useCallback((node: HTMLInputElement | null) => { if (node) { node.focus(); node.select(); } }, []);
  const trigger = useRef<HTMLButtonElement>(null);
  function finish(cancel = false) {
    if (finished.current) return;
    finished.current = true;
    const next = value.trim();
    if (!cancel && !disabled && next && next !== name) onChange(next);
    setEditing(false);
  }
  return <h1 className={styles.editableName}>
    {editing && !disabled ? <input aria-label="智能体名称" value={value}
      ref={focusInput}
      onChange={event => setValue(event.target.value)}
      onCompositionStart={() => { composing.current = true; }}
      onCompositionEnd={() => { composing.current = false; endedAt.current = Date.now(); }}
      onBlur={() => finish()}
      onKeyDown={event => {
        if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
        if (event.key === "Enter") {
          event.preventDefault(); event.stopPropagation();
          if (Date.now() - endedAt.current < 80) return;
          finish(); requestAnimationFrame(() => trigger.current?.focus());
        } else if (event.key === "Escape") {
          event.preventDefault(); event.stopPropagation();
          finish(true); requestAnimationFrame(() => trigger.current?.focus());
        }
      }} /> : disabled ? name : <button type="button" ref={trigger} title="编辑智能体名称" aria-label={`编辑智能体名称：${name}`}
      onClick={() => { setValue(name); finished.current = false; setEditing(true); }}>{name}</button>}
  </h1>;
}
