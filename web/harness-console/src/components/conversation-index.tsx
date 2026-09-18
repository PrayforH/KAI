"use client";

import { useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

type Entry = { id: string; label: string; answer: string; node: HTMLElement };

// The rail is the thread's time journey. Ticks stay readable when a task runs
// for many turns, so they are sized from a shared scale instead of magic
// numbers inline: idle, current, and the four neighbours of the hovered tick.
/** Reveal delay for a tick: the rail unfolds from its middle towards both ends. */
export function railRevealDelay(index: number, count: number, step = 26) {
  return `${Math.round(Math.abs(index - (count - 1) / 2) * step)}ms`;
}

const IDLE_LINE_WIDTH = 6;
const ACTIVE_LINE_WIDTH = 8;
const NEIGHBOUR_LINE_WIDTHS = [19, 14, 10, 7, 6];

/** Indexes user turns inside this thread only, including restored history and branches. */
export function ConversationIndex({ frame, threadId, pagination }: {
  frame: RefObject<HTMLDivElement | null>;
  threadId: string;
  pagination?: { total: number; hasMore: boolean; loadEarlier: () => Promise<void> };
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [active, setActive] = useState("");
  const [hovered, setHovered] = useState("");
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  // The unfold is an entrance, not a reaction: later updates (an auto-loaded page
  // turning pending ticks into loaded ones) rebuild tick elements, and without
  // this they would replay the whole animation.
  const [revealing, setRevealing] = useState(false);
  const hasTicks = (pagination?.total ?? entries.length) > 0;
  useEffect(() => {
    if (!hasTicks) { setRevealing(false); return; }
    setRevealing(true);
    const timer = window.setTimeout(() => setRevealing(false), 900);
    return () => window.clearTimeout(timer);
  }, [hasTicks]);
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
  // The thread's earlier runs are not materialised yet; show them as placeholder
  // ticks so the rail reflects the whole conversation instead of one page.
  async function revealPending(globalIndex: number) {
    if (!pagination) return;
    for (let attempt = 0; attempt < 12 && pagination.hasMore; attempt += 1) {
      const visible = frame.current?.querySelectorAll("[data-turn-id]").length ?? 0;
      const offset = Math.max(0, pagination.total - visible);
      if (globalIndex >= offset) {
        const node = frame.current?.querySelectorAll<HTMLElement>("[data-turn-id]")[globalIndex - offset];
        if (node) scrollToTurn(node);
        setHovered("");
        return;
      }
      await pagination.loadEarlier();
      await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
    }
  }

  function scrollToTurn(node: HTMLElement) {
    const viewport = frame.current?.querySelector<HTMLElement>(".aui-thread-viewport");
    if (!viewport) return;
    const top = node.getBoundingClientRect().top - viewport.getBoundingClientRect().top + viewport.scrollTop - 32;
    viewport.scrollTo({
      top: Math.max(0, top),
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth",
    });
    node.focus({ preventScroll: true });
  }

  const pending = Math.max(0, (pagination?.total ?? entries.length) - entries.length);
  return <nav className="conversation-index" data-expanded={Boolean(hovered)} aria-label="对话轮次索引" onMouseLeave={() => setHovered("")}>
    <div className="conversation-index-rail" data-revealing={revealing ? "true" : undefined}>
      {Array.from({ length: pending }, (_, index) => <button key={`pending-${index}`} type="button"
        className="conversation-index-pending" style={{ "--index-line-width": `${IDLE_LINE_WIDTH}px`, "--rail-reveal-delay": railRevealDelay(index, pending + entries.length) } as CSSProperties}
        aria-label={`第 ${index + 1} 轮：加载更早的轮次`}
        onClick={() => void revealPending(index)}
      ><span aria-hidden="true" /></button>)}
      {entries.map((entry, index) => <button key={entry.id} ref={node => { if (node) buttons.current.set(entry.id, node); else buttons.current.delete(entry.id); }} type="button" style={{ "--index-line-width": `${expandedIndex < 0 ? (entry.id === active ? ACTIVE_LINE_WIDTH : IDLE_LINE_WIDTH) : NEIGHBOUR_LINE_WIDTHS[Math.min(4, Math.abs(index - expandedIndex))]}px`, "--rail-reveal-delay": railRevealDelay(pending + index, pending + entries.length) } as CSSProperties} data-highlighted={entry.id === hovered} aria-label={`第 ${index + 1} 轮：${entry.label}`} aria-current={entry.id === active ? "location" : undefined} onMouseEnter={() => setHovered(entry.id)} onFocus={() => setHovered(entry.id)} onBlur={() => setHovered("")} onKeyDown={event => {
        const offset = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
        const target = event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1 : index + offset;
        if (offset || event.key === "Home" || event.key === "End") { event.preventDefault(); buttons.current.get(entries[Math.max(0, Math.min(entries.length - 1, target))].id)?.focus(); }
      }} onClick={() => {
        scrollToTurn(entry.node); setActive(entry.id); setHovered("");
      }}><span aria-hidden="true" /></button>)}
    </div>
    {preview && <div className="conversation-index-preview" style={{top: previewTop}} aria-hidden="true"><strong>{preview.label}</strong><p>{preview.answer || "这一轮暂无回答"}</p></div>}
  </nav>;
}
