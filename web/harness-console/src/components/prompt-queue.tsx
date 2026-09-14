"use client";
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
    {(items.length > 1 || paused) && <header><strong>待发送 · {items.length}</strong><span>{paused ? "已暂停" : ""}</span>
      <button type="button" disabled={sendingIds.length > 0 || Boolean(editing)} onClick={() => onPause(!paused)}>{paused ? "继续队列" : "暂停"}</button>
    </header>}
    <ol>{items.map((item, index) => {
      const sending = sendingIds.includes(item.id);
      return <li key={item.id}>
        {editing === item.id ? <div className="queue-editor">
          <textarea aria-label={`编辑第 ${index + 1} 条消息`} autoFocus value={draft} rows={2}
            onChange={(event) => setDraft(event.target.value)}
            onCompositionStart={() => { composing.current = true; }}
            onCompositionEnd={() => { composing.current = false; }}
            onKeyDown={(event) => {
              if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
              if (event.key === "Escape") { event.preventDefault(); setEditing(null); }
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) { event.preventDefault(); save(item); }
            }} />
          <div><button type="button" onClick={() => setEditing(null)}>取消</button><button type="button" disabled={!draft.trim() && !item.attachments.length} onClick={() => save(item)}>保存</button></div>
        </div> : <>
          <span className="queue-number" aria-hidden="true">↳</span>
          <span className="queue-copy" title={item.text}>{item.text || "附件消息"}{item.attachments.length > 0 && <small> · {item.attachments.length} 个附件</small>}</span>
          {busy ? <button type="button" disabled={!canSteer || sending || item.attachments.length > 0} title={canSteer ? "将补充送入当前运行" : "等待运行支持实时引导"} onClick={() => onGuide(item)}>{sending ? "正在引导…" : "↪ 调整方向"}</button>
            : <button type="button" disabled={sendingIds.length > 0} onClick={() => onSend(item)}>发送</button>}
          <button type="button" aria-label={`删除第 ${index + 1} 条`} disabled={sending} onClick={() => onChange(items.filter((entry) => entry.id !== item.id))}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 5.5h13M7 5.5V3h6v2.5M5 5.5l1 11h8l1-11M8 8v6m4-6v6" /></svg></button>
          <details className="queue-more"><summary aria-label={`更多第 ${index + 1} 条的操作`}>···</summary><div>
            <button type="button" disabled={sending} onClick={(event) => { event.currentTarget.closest("details")?.removeAttribute("open"); onPause(true); setDraft(item.text); setEditing(item.id); }}>编辑</button>
            <button type="button" aria-label={`上移第 ${index + 1} 条`} disabled={index === 0 || sendingIds.length > 0} onClick={() => move(index, -1)}>上移</button>
            <button type="button" aria-label={`下移第 ${index + 1} 条`} disabled={index === items.length - 1 || sendingIds.length > 0} onClick={() => move(index, 1)}>下移</button>
          </div></details>
        </>}
      </li>;
    })}</ol>
  </section>;
}
