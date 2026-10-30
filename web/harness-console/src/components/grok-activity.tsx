"use client";

import { useEffect, useRef, useState } from "react";

export type GrokFaceState = "idle" | "working" | "done" | "failed";

/**
 * 对齐客户端 GrokBotMark 的观感：blob 身体 + 两只眼睛。
 * - idle：约 4s 一次慢眨眼
 * - working：轻微上下浮动 + 眼睛随鼠标注视（attachMarkHoverGaze 的等价物）
 * - done / failed：一次弹跳定格
 * 颜色取当前 bot 的强调色（--kai-bot-accent），墨色与眼色走 CSS 变量。
 */
export function GrokFace({ state, size = 22 }: { state: GrokFaceState; size?: number }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  useEffect(() => {
    const node = ref.current;
    if (!node || state === "done" || state === "failed") return;
    let frame = 0;
    const onMove = (event: PointerEvent) => {
      if (frame) return;
      frame = window.requestAnimationFrame(() => {
        frame = 0;
        const rect = node.getBoundingClientRect();
        const dx = event.clientX - (rect.left + rect.width / 2);
        const dy = event.clientY - (rect.top + rect.height / 2);
        const scale = Math.hypot(dx, dy) || 1;
        // 客户端把注视幅度压到 0.5 倍；这里映射到 ±1.4px，避免夸张。
        const gazeX = Math.max(-1.4, Math.min(1.4, (dx / scale) * 1.4));
        const gazeY = Math.max(-1.2, Math.min(1.2, (dy / scale) * 1.2));
        node.style.setProperty("--gaze-x", `${gazeX.toFixed(2)}px`);
        node.style.setProperty("--gaze-y", `${gazeY.toFixed(2)}px`);
      });
    };
    const onLeave = () => {
      node.style.setProperty("--gaze-x", "0px");
      node.style.setProperty("--gaze-y", "0px");
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    window.addEventListener("pointerleave", onLeave);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerleave", onLeave);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [state]);
  const blink = state === "idle" || state === "done" || state === "failed";
  return (
    <span
      ref={ref}
      className="grok-face"
      data-state={state}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 64 64">
        <path
          className="grok-face-body"
          d="M32 6c11 0 19 7 19 17 0 9-7 13-7 22 0 7-6 11-12 11-7 0-12-4-12-12 0-9-7-12-7-21C13 13 21 6 32 6Z"
        />
        <g className="grok-face-eyes" style={{ transform: "translate(var(--gaze-x, 0px), var(--gaze-y, 0px))" }}>
          <ellipse className="grok-face-eye" data-blink={blink ? "true" : undefined} cx="26" cy="30" rx="2.6" ry="4.6" />
          <ellipse className="grok-face-eye" data-blink={blink ? "true" : undefined} cx="38" cy="30" rx="2.6" ry="4.6" />
        </g>
      </svg>
    </span>
  );
}

const PHRASE_STEP_MS = 90;

/**
 * 客户端 activityPhraseDraw 的等价物：短语按词渐进显现，
 * 已显现的词保持稳定 DOM（只追加，不重排），文本更新时从当前进度续播。
 */
export function ActivityPhrase({ text }: { text: string }) {
  const words = text ? text.split(/(\s+)/).filter(Boolean) : [];
  const [drawn, setDrawn] = useState(words.length);
  const previous = useRef(text);
  useEffect(() => {
    if (text === previous.current) return;
    const shared = sharedPrefixLength(previous.current, text);
    previous.current = text;
    setDrawn((current) => (current < shared ? shared : current));
    const total = text.split(/(\s+)/).filter(Boolean).length;
    const timer = window.setInterval(() => {
      setDrawn((current) => {
        if (current >= total) {
          window.clearInterval(timer);
          return current;
        }
        return current + 1;
      });
    }, PHRASE_STEP_MS);
    return () => window.clearInterval(timer);
  }, [text]);
  return (
    <span className="grok-phrase">
      {words.slice(0, drawn).map((word, index) => (
        <span className="grok-phrase-word" key={`${index}:${word}`} data-new={index === drawn - 1 ? "true" : undefined}>
          {word}
        </span>
      ))}
      {drawn < words.length ? <span className="grok-phrase-caret" aria-hidden="true" /> : null}
    </span>
  );
}

function sharedPrefixLength(before: string, after: string): number {
  const left = before.split(/(\s+)/).filter(Boolean);
  const right = after.split(/(\s+)/).filter(Boolean);
  let count = 0;
  while (count < left.length && count < right.length && left[count] === right[count]) count += 1;
  return count;
}

/** 状态行：脸 + 渐现短语，占对话流里的一行（客户端的活动行形态）。 */
export function GrokActivityRow({
  phrase,
  state,
}: {
  phrase: string;
  state: GrokFaceState;
}) {
  return (
    <div className="grok-activity-row" data-state={state} aria-live="polite">
      <GrokFace state={state} />
      <ActivityPhrase text={phrase} />
    </div>
  );
}
