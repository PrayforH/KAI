"use client";

import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

type Entry = { id: string; label: string; answer: string; node: HTMLElement };

/** Indexes user turns inside this thread only, including restored history and branches. */
export function ConversationIndex({ frame, threadId }: { frame: RefObject<HTMLDivElement | null>; threadId: string }) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [active, setActive] = useState("");
  const [hovered, setHovered] = useState("");
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => {
    const root = frame.current;
    if (!root) return;
    setHovered(""); setEntries([]); setActive("");
    let current: Entry[] = [];
    let viewport: HTMLElement | null = null;
    let raf = 0;
    function track() {
      if (!viewport || !current.length) return;
      const threshold = viewport.getBoundingClientRect().top + 72;
      let selected = current[0].id;
      for (const entry of current) {
        if (entry.node.getBoundingClientRect().top > threshold) break;
        selected = entry.id;
      }
      setActive(selected);
    }
    function refresh() {
      raf = 0;
      const nextViewport = root!.querySelector<HTMLElement>(".aui-thread-viewport");
      if (nextViewport !== viewport) {
        viewport?.removeEventListener("scroll", track);
        viewport = nextViewport;
        viewport?.addEventListener("scroll", track, { passive: true });
      }
      const next: Entry[] = [];
      for (const node of root!.querySelectorAll<HTMLElement>("[data-turn-id], [data-turn-answer]")) {
        if (node.dataset.turnId) next.push({ id: node.dataset.turnId, label: node.dataset.turnLabel || "附件消息", answer: "", node });
        else if (next.length && node.dataset.turnAnswer) next[next.length - 1].answer = node.dataset.turnAnswer.replace(/!?(\[([^\]]+)\])\([^)]*\)/g, "$2").replace(/[#*_`>]/g, "").trim();
      }
      const changed = next.length !== current.length || next.some((entry, i) => entry.id !== current[i]?.id || entry.label !== current[i]?.label || entry.answer !== current[i]?.answer || entry.node !== current[i]?.node);
      current = next;
      if (changed) setEntries(next);
      track();
    }
    function schedule() { if (!raf) raf = requestAnimationFrame(refresh); }
    const mutation = new MutationObserver(schedule);
    mutation.observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-turn-id", "data-turn-label", "data-turn-answer"] });
    const resize = new ResizeObserver(schedule);
    resize.observe(root);
    refresh();
    return () => { mutation.disconnect(); resize.disconnect(); viewport?.removeEventListener("scroll", track); if (raf) cancelAnimationFrame(raf); };
  }, [frame, threadId]);
  useEffect(() => {
    const button = buttons.current.get(active);
    const rail = button?.parentElement;
    if (button && rail) {
      if (button.offsetTop < rail.scrollTop) rail.scrollTop = button.offsetTop;
      else if (button.offsetTop + button.offsetHeight > rail.scrollTop + rail.clientHeight) rail.scrollTop = button.offsetTop + button.offsetHeight - rail.clientHeight;
    }
  }, [active]);
  if (!entries.length) return null;
  const preview = entries.find(entry => entry.id === hovered);
  const expandedIndex = entries.findIndex(entry => entry.id === hovered);
  const previewButton = buttons.current.get(hovered);
  const previewTop = previewButton ? Math.max(60, Math.min((previewButton.parentElement?.clientHeight ?? 120) - 60, previewButton.offsetTop - (previewButton.parentElement?.scrollTop ?? 0) + 5)) : 60;
  return <nav className="conversation-index" data-expanded={Boolean(hovered)} aria-label="对话轮次索引" onMouseLeave={() => setHovered("")}>
    <div className="conversation-index-rail">
      {entries.map((entry, index) => <button key={entry.id} ref={node => { if (node) buttons.current.set(entry.id, node); else buttons.current.delete(entry.id); }} type="button" style={{ "--index-line-width": `${expandedIndex < 0 ? (entry.id === active ? 6 : 4) : [16, 12, 9, 6, 4][Math.min(4, Math.abs(index - expandedIndex))]}px` } as CSSProperties} data-highlighted={entry.id === hovered} aria-label={`第 ${index + 1} 轮：${entry.label}`} aria-current={entry.id === active ? "location" : undefined} onMouseEnter={() => setHovered(entry.id)} onFocus={() => setHovered(entry.id)} onBlur={() => setHovered("")} onKeyDown={event => {
        const offset = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
        const target = event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1 : index + offset;
        if (offset || event.key === "Home" || event.key === "End") { event.preventDefault(); buttons.current.get(entries[Math.max(0, Math.min(entries.length - 1, target))].id)?.focus(); }
      }} onClick={() => {
        const viewport = frame.current?.querySelector<HTMLElement>(".aui-thread-viewport");
        if (!viewport) return;
        const top = entry.node.getBoundingClientRect().top - viewport.getBoundingClientRect().top + viewport.scrollTop - 32;
        viewport.scrollTo({ top: Math.max(0, top), behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
        entry.node.focus({ preventScroll: true }); setActive(entry.id); setHovered("");
      }}><span aria-hidden="true" /></button>)}
    </div>
    {preview && <div className="conversation-index-preview" style={{top: previewTop}} aria-hidden="true"><strong>{preview.label}</strong><p>{preview.answer || "这一轮暂无回答"}</p></div>}
  </nav>;
}
