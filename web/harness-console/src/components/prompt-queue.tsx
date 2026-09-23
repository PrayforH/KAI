"use client";
import { ConversationControl } from "./conversation-control";
import { useRef, useState } from "react";
import type { QueuedPrompt } from "../lib/composer-interactions";

export function PromptQueue({ items, paused, busy, canSteer, sendingIds, onChange, onPause, onGuide, onSend }: {
  items: QueuedPrompt[]; paused: boolean; busy: boolean; canSteer: boolean; sendingIds: string[];
  onChange: (items: QueuedPrompt[]) => void; onPause: (paused: boolean) => void;
  onGuide: (item: QueuedPrompt) => void; onSend: (item: QueuedPrompt) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const composing = useRef(false);
  function save(item: QueuedPrompt) {
    if (composing.current || (!draft.trim() && !item.attachments.length)) return;
    onChange(items.map((entry) => entry.id === item.id ? { ...entry, text: draft.trim() } : entry));
    setEditing(null);
  }
  function move(index: number, offset: number) {
    const next = [...items];
    [next[index], next[index + offset]] = [next[index + offset], next[index]];
    onChange(next);
  }
  if (!items.length) return null;
  return <section className="composer-context-shelf prompt-queue" aria-label="待发送队列">
    <ol>{items.map((item, index) => {
      const sending = sendingIds.includes(item.id);
      return <li key={item.id} tabIndex={0} title="Alt + ↑ / ↓ 调整队列顺序" onKeyDown={event => {
        if (!event.altKey || sendingIds.length || editing) return;
        const offset = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
        if (offset && index + offset >= 0 && index + offset < items.length) { event.preventDefault(); move(index, offset); }
      }}>
        {editing === item.id ? <div className="queue-editor">
          <textarea aria-label={`编辑第 ${index + 1} 条消息`} autoFocus value={draft} rows={1}
            onChange={(event) => setDraft(event.target.value)}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={() => { composing.current = false; }}
            onKeyDown={(event) => {
              if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
              if (event.key === "Escape") { event.preventDefault(); setEditing(null); }
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); save(item); }
            }} />
          <button type="button" aria-label="取消编辑" title="取消" onClick={() => setEditing(null)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg></button><button type="button" aria-label="保存编辑" title="保存" disabled={!draft.trim() && !item.attachments.length} onClick={() => save(item)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 10 4 4 8-8" /></svg></button>
        </div> : <>
          <span className="queue-number" aria-hidden="true">↳</span>
          <span className="queue-copy" title={item.text}>{item.text || "附件消息"}{item.attachments.length > 0 && <small> · {item.attachments.length} 个附件</small>}</span>
          <div className="queue-actions">
          {paused && index === 0 && <ConversationControl action="resume" aria-label="继续队列" disabled={sendingIds.length > 0 || Boolean(editing)} onClick={() => onPause(false)} />}
          {busy ? <button type="button" disabled={!canSteer || sending || item.attachments.length > 0} title={canSteer ? "将补充送入当前运行" : "等待运行支持实时引导"} onClick={() => onGuide(item)} aria-label={sending ? "正在调整方向" : "调整方向"}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M4 16V9a4 4 0 0 1 4-4h8m-4-4 4 4-4 4" /></svg></button>
            : <ConversationControl action="send" aria-label="发送" disabled={sendingIds.length > 0} onClick={() => onSend(item)} />}
          <button type="button" aria-label={`删除第 ${index + 1} 条`} disabled={sending} onClick={() => onChange(items.filter((entry) => entry.id !== item.id))}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 5.5h13M7 5.5V3h6v2.5M5 5.5l1 11h8l1-11M8 8v6m4-6v6" /></svg></button>
          <button type="button" aria-label={`编辑第 ${index + 1} 条`} title="编辑" disabled={sending} onClick={() => { onPause(true); setDraft(item.text); setEditing(item.id); }}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m12 4 4 4M4 12l9-9a2 2 0 0 1 3 3l-9 9-4 1 1-4Z" /></svg></button>
          </div>
        </>}
      </li>;
    })}</ol>
  </section>;
}
