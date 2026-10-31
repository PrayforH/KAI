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
  const short = phrase.length > MAX_PHRASE_CHARS
    ? `${phrase.slice(0, MAX_PHRASE_CHARS)}…`
    : phrase;
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

/** 正文类事件（message.*）与思考类事件不进活动行：它们是回答本体，不是进展。 */
const PROSE_EVENT_PREFIXES = ["message.", "reasoning."];
const MAX_PHRASE_CHARS = 60;

/**
 * 一条活动条目能当"进展"用的文本。
 *
 * 后端的 tool.request / tool.result 把文案写在 title（「调用 Shell」
 * 「工具调用完成」），summary 为 null；runtime.system 才写 summary
 * （「正在等待本轮模型结果」）。只看 summary 会让工具阶段永远停在等待文案。
 * tool.result 另有 metadata.result_summary（「返回 12 行 · 340 字符」），
 * 比通用的「工具调用完成」更有信息，优先使用。
 */
function itemPhrase(item: ActivityLike): string {
  const summary = (item.summary ?? "").trim();
  if (summary) return summary;
  if (item.event_type === "tool.result" || item.event_type === "tool.allowed") {
    const result = item.metadata?.result_summary;
    if (typeof result === "string" && result.trim()) return result.trim();
  }
  return (item.title ?? "").trim();
}

/**
 * 活动行的短语：只取真正的进展条目（runtime/tool/guidance/compaction 等）。
 *
 * 后端把每条 message.delta 也记成一条 activity item，summary 就是当轮正文
 * （src/harness/agui/activity.py:516）。照着"最新带 summary 的条目"取短语，
 * 会把整段回答画在脸旁边，与 .assistant-answer 气泡重复——历史线程硬刷新必现。
 */
export function progressPhrase(
  activity: { items: ReadonlyArray<ActivityLike> },
  options: { allowTransient?: boolean } = {},
): string {
  const allowTransient = options.allowTransient ?? true;
  const items = [...activity.items].reverse();
  for (const item of items) {
    const type = item.event_type ?? "";
    if (type.startsWith("run.")) continue;
    if (PROSE_EVENT_PREFIXES.some((prefix) => type.startsWith(prefix))) continue;
    const phrase = itemPhrase(item);
    if (!phrase) continue;
    if (phrase.length > MAX_PHRASE_CHARS) continue;
    if (!allowTransient && isTransientProgress(phrase)) continue;
    return phrase;
  }
  return "";
}

/** 过程摘录只收这几类：工具调用/结果、运行环境、上下文压缩。 */
const STEP_EVENT_PREFIXES = ["tool.", "runtime.", "context."];
const MAX_STEP_CHARS = 80;
const MAX_STEPS = 5;

export type ChatActivityStep = {
  id: string;
  text: string;
  tone: "tool" | "runtime" | "failed";
};

/**
 * 助理面活动行下方的精简过程摘录（最近 ≤5 条）。
 * 只取带文本的 tool.* / runtime.* / context.* 条目，跳过纯等待文案与正文；
 * 与主短语相同的文本不重复列（否则「调用 Shell」会在两行里各出现一次）。
 */
export function activitySteps(
  activity: { items: ReadonlyArray<ActivityLike> },
  options: { limit?: number; excludeText?: string } = {},
): ChatActivityStep[] {
  const limit = options.limit ?? MAX_STEPS;
  const exclude = (options.excludeText ?? "").trim();
  const picked: ChatActivityStep[] = [];
  for (const item of [...activity.items].reverse()) {
    if (picked.length >= limit) break;
    const type = item.event_type ?? "";
    if (!STEP_EVENT_PREFIXES.some((prefix) => type.startsWith(prefix))) continue;
    const text = itemPhrase(item);
    if (!text || text.length > MAX_STEP_CHARS) continue;
    if (isTransientProgress(text)) continue;
    if (exclude && text === exclude) continue;
    if (picked.some((step) => step.text === text)) continue;
    picked.push({
      id: typeof item.id === "string" && item.id ? item.id : `${type}:${picked.length}:${text}`,
      text,
      tone:
        item.status === "failed" ? "failed"
        : type.startsWith("tool.") ? "tool"
        : "runtime",
    });
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
