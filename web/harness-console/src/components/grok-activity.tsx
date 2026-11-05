"use client";

import { useEffect, useRef, useState } from "react";
import { useAssistantBot } from "./assistant-bot-context";
import { botShape } from "./bot-rail";
import { activitySteps, statusPhrase, type ActivityLike } from "../lib/assistant-progress";

export type GrokFaceState = "idle" | "working" | "done" | "failed";

/** 不同外形下的眼睛位置/大小；树、水滴等窄外形把眼睛收一点，保证可读。 */
const EYE_GEOMETRY: Record<string, { cx: number; cy: number; rx: number; ry: number }> = {
  blob: { cx: 26, cy: 30, rx: 2.6, ry: 4.6 },
  star: { cx: 28.6, cy: 29, rx: 2.1, ry: 3.7 },
};

/**
 * 对齐客户端 GrokBotMark 的观感：保存的外形（色 + 形）+ 两只眼睛。
 * - 外形取 config.appearance.avatar（缺/未知 → blob），与 rail / 顶栏同源；
 * - idle：约 4s 一次慢眨眼
 * - working：轻微上下浮动 + 眼睛随鼠标注视（attachMarkHoverGaze 的等价物）
 * - done / failed：一次弹跳定格
 * 颜色取当前 bot 的强调色（--kai-bot-accent），墨色与眼色走 CSS 变量。
 */
export function GrokFace({
  state,
  size = 22,
  avatar,
}: {
  state: GrokFaceState;
  size?: number;
  avatar?: string | null;
}) {
  const assistantBot = useAssistantBot();
  const glyph = botShape(avatar ?? assistantBot?.appearance?.avatar);
  const eye = EYE_GEOMETRY[glyph.id] ?? EYE_GEOMETRY.blob!;
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
      data-avatar={glyph.id}
      style={{ width: size, height: size }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 64 64">
        <path className="grok-face-body" d={glyph.path} />
        <g className="grok-face-eyes" style={{ transform: "translate(var(--gaze-x, 0px), var(--gaze-y, 0px))" }}>
          <ellipse
            className="grok-face-eye"
            data-blink={blink ? "true" : undefined}
            cx={eye.cx}
            cy={eye.cy}
            rx={eye.rx}
            ry={eye.ry}
          />
          <ellipse
            className="grok-face-eye"
            data-blink={blink ? "true" : undefined}
            cx={64 - eye.cx}
            cy={eye.cy}
            rx={eye.rx}
            ry={eye.ry}
          />
        </g>
      </svg>
    </span>
  );
}

const PHRASE_STEP_MS = 90;

const CJK_CHAR = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/;

type GraphemeSegmenter = new (
  locale?: string,
  options?: { granularity: "grapheme" },
) => { segment(input: string): Iterable<{ segment: string }> };

/** 按字素切分（emoji/组合字符不会被切成半个）；环境无 Segmenter 时退回码点。 */
function splitGraphemes(value: string): string[] {
  const Segmenter = (Intl as unknown as { Segmenter?: GraphemeSegmenter }).Segmenter;
  if (Segmenter) {
    const segmenter = new Segmenter("zh", { granularity: "grapheme" });
    return Array.from(segmenter.segment(value), (part) => part.segment);
  }
  return Array.from(value);
}

/**
 * 短语的渐现单位：中文没有空格，按词切会整句一次跳出；
 * 所以 CJK 为主（或无空白但含 CJK）时按字素逐个显现，
 * 纯英文（含单个单词）仍按词，保持「词级」而不是被拆成字母。
 */
export function phraseUnits(text: string): string[] {
  if (!text) return [];
  const graphemes = splitGraphemes(text);
  const cjk = graphemes.filter((unit) => CJK_CHAR.test(unit)).length;
  const hasWhitespace = /\s/.test(text);
  const cjkShare = cjk / Math.max(1, graphemes.length);
  if (cjk > 0 && (cjkShare > 0.5 || !hasWhitespace)) return graphemes;
  return text.split(/(\s+)/).filter(Boolean);
}

/**
 * 客户端 activityPhraseDraw 的等价物：短语按单位渐进显现，
 * 已显现的单位保持稳定 DOM（只追加，不重排），文本更新时从当前进度续播。
 */
export function ActivityPhrase({ text, instant = false }: { text: string; instant?: boolean }) {
  const units = phraseUnits(text);
  const [drawn, setDrawn] = useState(units.length);
  const previous = useRef(text);
  useEffect(() => {
    if (instant || text === previous.current) return;
    const next = phraseUnits(text);
    const shared = sharedPrefixLength(phraseUnits(previous.current), next);
    previous.current = text;
    setDrawn(shared);
    if (shared >= next.length) return;
    const timer = window.setInterval(() => {
      setDrawn((current) => {
        if (current >= next.length) {
          window.clearInterval(timer);
          return current;
        }
        return current + 1;
      });
    }, PHRASE_STEP_MS);
    return () => window.clearInterval(timer);
  }, [text, instant]);
  return (
    <span className="grok-phrase">
      {instant ? text : units.slice(0, drawn).map((unit, index) => (
        <span className="grok-phrase-word" key={`${index}:${unit}`} data-new={index === drawn - 1 ? "true" : undefined}>
          {unit}
        </span>
      ))}
      {!instant && drawn < units.length ? <span className="grok-phrase-caret" aria-hidden="true" /> : null}
    </span>
  );
}

function sharedPrefixLength(before: readonly string[], after: readonly string[]): number {
  let count = 0;
  while (count < before.length && count < after.length && before[count] === after[count]) count += 1;
  return count;
}

/**
 * 助理状态行：当前 Bot 的图标 + 一句短阶段 + 扫光。
 * 主助理与子助理共用同一套外观（appearance），不另开卡片。
 */
export function GrokActivityRow({
  phrase,
  state,
  avatar,
}: {
  phrase: string;
  state: GrokFaceState;
  avatar?: string | null;
}) {
  const short = statusPhrase(phrase);
  return (
    <div className="grok-activity-row" data-state={state} role="status" aria-label="处理进展">
      <GrokFace state={state} avatar={avatar} />
      <ActivityPhrase text={short} instant />
    </div>
  );
}


export { activitySteps, progressPhrase, shouldShowChatActivityRow, shouldShowChatActivitySteps, isTransientProgress, statusPhrase, trimConclusion } from "../lib/assistant-progress";
export type { ActivityLike, ChatActivityStep } from "../lib/assistant-progress";

/** 助理面的过程摘录：一行一条，轻量小列表，不做任务面那种整块面板。 */
export function ChatActivitySteps({
  activity,
  phrase,
}: {
  activity: { items: ReadonlyArray<ActivityLike> };
  phrase: string;
}) {
  const steps = activitySteps(activity, { excludeText: phrase });
  if (!steps.length) return null;
  return (
    <ul className="chat-activity-steps" aria-label="过程摘录">
      {steps.map((step) => (
        <li key={step.id} data-tone={step.tone}>
          {step.text}
        </li>
      ))}
    </ul>
  );
}
