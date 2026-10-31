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

const CJK_CHAR = /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/;

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
export function ActivityPhrase({ text }: { text: string }) {
  const units = phraseUnits(text);
  const [drawn, setDrawn] = useState(units.length);
  const previous = useRef(text);
  useEffect(() => {
    if (text === previous.current) return;
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
  }, [text]);
  return (
    <span className="grok-phrase">
      {units.slice(0, drawn).map((unit, index) => (
        <span className="grok-phrase-word" key={`${index}:${unit}`} data-new={index === drawn - 1 ? "true" : undefined}>
          {unit}
        </span>
      ))}
      {drawn < units.length ? <span className="grok-phrase-caret" aria-hidden="true" /> : null}
    </span>
  );
}

function sharedPrefixLength(before: readonly string[], after: readonly string[]): number {
  let count = 0;
  while (count < before.length && count < after.length && before[count] === after[count]) count += 1;
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
  // 行内再走一次 10 字口径：即便有调用方塞进长段落，也不会铺满一行。
  const capped = phrase.length > MAX_PHRASE_CHARS
    ? phrase.slice(0, MAX_PHRASE_CHARS)
    : phrase;
  const short = trimConclusion(capped);
  return (
    <div className="grok-activity-row" data-state={state} aria-live="polite">
      <GrokFace state={state} />
      <ActivityPhrase text={short} />
    </div>
  );
}


/**
 * 瞬时等待/进行中的状态短语：只在回合进行中有意义，终态后必须消失
 * （对齐客户端：交办或回合结束后中间状态不留在历史流里）。
 */
const TRANSIENT_PROGRESS = [
  "正在等待本轮模型结果",
  "正在生成回复",
  "正在准备运行环境",
  "正在准备运行",
  "正在整理本轮结果",
  "正在生成可恢复的上下文摘要",
  "正在处理",
];

export function isTransientProgress(text: string): boolean {
  const value = text.trim();
  return TRANSIENT_PROGRESS.some((phrase) => value === phrase || value.startsWith(`${phrase}（`));
}

/** 只有这些 Run 状态才有"进行中的活动行"；终态一律不画。 */
const ACTIVE_PROGRESS_STATUSES = new Set([
  "queued",
  "pending",
  "provisioning",
  "running",
  "waiting",
  "waiting_approval",
  "cancelling",
]);

/** 聊天形态活动行的唯一开关：有合格进展，且回合仍未结束。 */
export function shouldShowChatActivityRow(status: string, activity: { items: ReadonlyArray<ActivityLike> }, options: { allowTransient?: boolean } = {}): boolean {
  const phrase = progressPhrase(activity, options);
  if (!phrase) return false;
  return ACTIVE_PROGRESS_STATUSES.has(status);
}

export type ActivityLike = {
  id?: string | null;
  event_type: string;
  title?: string | null;
  summary?: string | null;
  status?: string | null;
  metadata?: Record<string, unknown> | null;
};

const MAX_PHRASE_CHARS = 160;

/**
 * 活动行要展示的是"工具之间的业务结论"（收到快捷操作… / 参数已确认…），
 * 不是工具调用日志。结论语来自 message.delta / reasoning.summary.delta，
 * 但只取**末次工具调用之前**的那些：末次工具之后的 message.* 是本轮终答，
 * 已经在回答气泡里，不能再用活动行复述一遍（dce054a7 的重复问题）。
 */
const CONCLUSION_EVENT_TYPES = ["message.delta", "reasoning.summary.delta"];
const PROCESS_EVENT_PREFIXES = ["tool.", "runtime.", "context."];
/** 源文本上限：整篇终答通常远超它，天然被挡在外面。 */
const MAX_CONCLUSION_CHARS = 160;
/**
 * 展示口径：对齐本机 Grok Bot 的极短结论语——约 10 个汉字宽（ASCII 半角按 0.5 计，
 * 即英文约 20 字符）。宁可截断，也不让长段落占住活动行。
 */
const DISPLAY_CONCLUSION_CHARS = 10;
const MAX_STEPS = 5; 

/** 工具日志/引导类文案：即使没有结论语也不拿来当主展示。 */
const GUIDANCE_PATTERNS = [
  /^调用\s/,
  /^工具调用/,
  /^已加载\s*\d+\s*项/,
  /^\d+\s*项工具/,
  /^运行环境/,
  /^正在准备运行/,
];

/** 可以在 10 字预算内收口的从句边界：优先停在「收到快捷操作：」这种可读处。 */
const CLAUSE_BOUNDARIES = new Set(["。", "！", "？", "；", "：", "，", "!", "?", ";", ":", ","]);

function normalizeText(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

function isGuidanceText(text: string): boolean {
  return GUIDANCE_PATTERNS.some((pattern) => pattern.test(text));
}

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

/** 显示宽度：ASCII 半角 0.5，汉字/全角 1。 */
function graphemeWidth(grapheme: string): number {
  return /^[\x00-\x7F]+$/.test(grapheme) ? grapheme.length / 2 : 1;
}

/**
 * 结论语展示：最多约 10 个汉字宽。
 * 优先停在句子/从句边界（「收到快捷操作：」），否则硬截并加省略号。
 */
export function trimConclusion(text: string, limit = DISPLAY_CONCLUSION_CHARS): string {
  const value = normalizeText(text);
  const units = splitGraphemes(value);
  let width = 0;
  let fitting = 0;
  let boundaryAt = 0;
  let boundaryWidth = 0;
  for (const unit of units) {
    const next = width + graphemeWidth(unit);
    if (next > limit) break;
    width = next;
    fitting += 1;
    if (CLAUSE_BOUNDARIES.has(unit)) {
      boundaryAt = fitting;
      boundaryWidth = width;
    }
  }
  if (fitting >= units.length) return value;
  if (boundaryAt > 0 && boundaryWidth >= limit * 0.4) {
    return units.slice(0, boundaryAt).join("").trimEnd();
  }
  // 硬截时给省略号留一个宽度单位（汉字则 9 字 + …，英文约 18 字符 + …）。
  let kept = 0;
  let used = 0;
  for (const unit of units) {
    const next = used + graphemeWidth(unit);
    if (next + 1 > limit) break;
    used = next;
    kept += 1;
  }
  return `${units.slice(0, kept).join("").trimEnd()}…`;
}

type Conclusion = { id: string; text: string };

/** 末次工具/runtime 事件的位置；其后的 message.* 是终答，不属于过程。 */
function lastProcessIndex(items: ReadonlyArray<ActivityLike>): number {
  let last = -1;
  items.forEach((item, index) => {
    const type = item.event_type ?? "";
    if (PROCESS_EVENT_PREFIXES.some((prefix) => type.startsWith(prefix))) last = index;
  });
  return last;
}

/**
 * 结论语池：末次工具之前的短 message.delta（业务摘要）。
 * 有 message 结论时不再混入 reasoning 摘要，避免思考碎片与结论语抢同一行。
 */
function conclusionPool(
  activity: { items: ReadonlyArray<ActivityLike> },
): Array<Conclusion & { kind: "message" | "reasoning" }> {
  const items = activity.items;
  const boundary = lastProcessIndex(items);
  const collected: Array<Conclusion & { kind: "message" | "reasoning" }> = [];
  const seen = new Set<string>();
  items.forEach((item, index) => {
    if (index >= boundary) return;
    const type = item.event_type ?? "";
    if (!CONCLUSION_EVENT_TYPES.includes(type)) return;
    const raw = normalizeText(item.summary ?? "");
    if (!raw || raw.length > MAX_CONCLUSION_CHARS) return;
    if (isGuidanceText(raw)) return;
    const text = trimConclusion(raw);
    if (!text || seen.has(text)) return;
    seen.add(text);
    collected.push({
      id: typeof item.id === "string" && item.id ? item.id : `${type}:${index}`,
      text,
      kind: type.startsWith("message.") ? "message" : "reasoning",
    });
  });
  const messages = collected.filter((entry) => entry.kind === "message");
  return messages.length ? messages : collected;
}

/**
 * 工具/运行条目的可回落文本。tool.request 只有「调用 X」这类日志，不参与；
 * tool.result 优先 metadata.result_summary（「返回 12 行 · 340 字符」），
 * runtime 用 summary（等待/压缩等即时状态）。
 */
function fallbackText(item: ActivityLike): string {
  const type = item.event_type ?? "";
  if (type.startsWith("tool.")) {
    if (type === "tool.request") return "";
    const result = item.metadata?.result_summary;
    if (typeof result === "string" && result.trim()) return normalizeText(result);
    if (type === "tool.result" || type === "tool.allowed") {
      return normalizeText(item.summary ?? "");
    }
    return "";
  }
  return normalizeText(item.summary ?? "");
}

/**
 * 活动行的短语：优先最新的中间结论语；没有结论语时只回落一句非引导的进展
 * （工具调用名、工具数量、运行环境提示都不当主展示）。
 * 终态是否渲染由 shouldShowChatActivityRow 决定，这里不掺入 status。
 */
export function progressPhrase(
  activity: { items: ReadonlyArray<ActivityLike> },
  options: { allowTransient?: boolean } = {},
): string {
  const allowTransient = options.allowTransient ?? true;
  const pool = conclusionPool(activity);
  for (let index = pool.length - 1; index >= 0; index -= 1) {
    const text = pool[index].text;
    if (!allowTransient && isTransientProgress(text)) continue;
    return text;
  }

  const items = [...activity.items].reverse();
  for (const item of items) {
    const type = item.event_type ?? "";
    if (type.startsWith("run.")) continue;
    if (CONCLUSION_EVENT_TYPES.includes(type)) continue;
    if (!PROCESS_EVENT_PREFIXES.some((prefix) => type.startsWith(prefix))) continue;
    const text = fallbackText(item);
    if (!text || text.length > MAX_PHRASE_CHARS) continue;
    if (isGuidanceText(text)) continue;
    if (!allowTransient && isTransientProgress(text)) continue;
    return trimConclusion(text);
  }
  return "";
}

export type ChatActivityStep = {
  id: string;
  text: string;
  tone: "note";
};

/**
 * 助理面活动行下方的过程摘录（最近 ≤5 条结论语）。
 * 只列结论语；工具调用日志不进来。与主短语相同的文本不重复列。
 */
export function activitySteps(
  activity: { items: ReadonlyArray<ActivityLike> },
  options: { limit?: number; excludeText?: string } = {},
): ChatActivityStep[] {
  const limit = options.limit ?? MAX_STEPS;
  const exclude = normalizeText(options.excludeText ?? "");
  const pool = conclusionPool(activity);
  const picked: ChatActivityStep[] = [];
  for (let index = pool.length - 1; index >= 0 && picked.length < limit; index -= 1) {
    const entry = pool[index];
    if (exclude && entry.text === exclude) continue;
    if (picked.some((step) => step.text === entry.text)) continue;
    picked.push({ id: entry.id, text: entry.text, tone: "note" });
  }
  return picked.reverse();
}

/**
 * 步骤条是否渲染：回合进行中、主短语成立、且过滤后还有内容。
 * 终态与活动行一起消失（0ee6fffd 的规则），不会钉进历史。
 */
export function shouldShowChatActivitySteps(
  status: string,
  activity: { items: ReadonlyArray<ActivityLike> },
  options: { allowTransient?: boolean } = {},
): boolean {
  if (!shouldShowChatActivityRow(status, activity, options)) return false;
  return activitySteps(activity, { excludeText: progressPhrase(activity, options) }).length > 0;
}

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
