// Bot 外观的统一取值：颜色与形状只是呈现，不参与任何运行判定。
// 侧栏、顶栏、来源行、@候选与外观预览都从这里取同一份 token，
// 不在各处分别硬编码颜色补丁。
//
// 取色表由服务端持有（GET /v1/studio/assistant/specialists/appearance/palette），
// 这里只保留 SVG 路径——形状是客户端渲染资源。
export const BOT_SHAPES = [
  { id: "blob", path: "M32 6c10 0 18 6 18 16 0 8-6 12-6 20 0 6-5 10-12 10-8 0-12-4-12-11 0-8-6-11-6-19C14 12 22 6 32 6Z" },
  { id: "drop", path: "M32 5c9 10 16 18 16 27a16 16 0 0 1-32 0c0-9 7-17 16-27Z" },
  { id: "block", path: "M18 10h28a8 8 0 0 1 8 8v28a8 8 0 0 1-8 8H18a8 8 0 0 1-8-8V18a8 8 0 0 1 8-8Z" },
  { id: "star", path: "M32 4c4 10 6 12 16 16-10 4-12 6-16 16-4-10-6-12-16-16 10-4 12-6 16-16Z" },
  { id: "cloud", path: "M18 44c-7 0-12-5-12-11 0-5 4-9 9-10 1-7 7-12 15-12s14 5 15 12c5 1 9 5 9 10 0 6-5 11-12 11Z" },
  { id: "pebble", path: "M34 6c11 2 18 11 16 22s-9 20-20 20S8 40 8 28 21 4 34 6Z" },
  { id: "hexagon", path: "M29 5a6 6 0 0 1 6 0l18 11a6 6 0 0 1 3 5v22a6 6 0 0 1-3 5L35 59a6 6 0 0 1-6 0L11 48a6 6 0 0 1-3-5V21a6 6 0 0 1 3-5Z" },
  { id: "petal", path: "M32 8c12-12 25 1 20 13 15 4 14 22 0 26-1 16-20 17-25 5C13 62 2 47 10 36-1 25 12 9 24 15Z" },
  { id: "rounded-triangle", path: "M19 6c6-3 11 0 16 4l22 22c7 8 3 15-7 19L19 60C8 63 4 57 6 46l5-28c1-6 3-10 8-12Z" },
  { id: "capsule", path: "M18 12h28c12 0 14 11 12 23S44 53 31 54 5 47 5 34 7 12 18 12Z" },
  { id: "leaf", path: "M12 9C31 1 54 11 57 28S41 59 25 55 1 34 8 20Z" },
  { id: "diamond", path: "M26 5c4-4 8-4 12 0l21 21c4 4 4 8 0 12L38 59c-4 4-8 4-12 0L5 38c-4-4-4-8 0-12Z" },
  { id: "comet", path: "M14 3c10-1 16 11 29 13 14 3 19 13 13 25S34 63 21 57 5 35 7 22 7 4 14 3Z" },
  { id: "soft-square", path: "M16 6h32c7 0 10 5 10 12v28c0 8-5 12-12 12H18C9 58 6 53 6 45V18C6 9 9 6 16 6Z" },
] as const;

/** 主助理的固定强调色；也是所有外观缺失时的兜底值。 */
export const DEFAULT_BOT_ACCENT = "#126fd4";

/** 白色等浅色外观的中性灰底：像 Grok 那样，浅色本体坐在灰色底板上。 */
export const NEUTRAL_SURFACE = "#8e8e93";
/** 浅色本体上的眼睛/描边必须是深墨色，否则白底白眼看不见。 */
export const NEUTRAL_INK = "#151515";

export type BotAppearanceTokens = {
  /** 原始强调色（填充本体）。 */
  accent: string;
  /** 浅色本体需要中性底板；深色本体保持透明。 */
  surface: string;
  /** 本体上的眼睛/描边色。 */
  ink: string;
  /** 浅色底上的可读文字色（名字、标签）。 */
  text: string;
  /** 深色底上的可读文字色。 */
  textOnDark: string;
  /** 用户气泡底色：浅色本体不再用白底白字。 */
  bubble: string;
  /** 用户气泡文字色。 */
  bubbleInk: string;
  /** 本体是不是浅色（需要底板与深墨）。 */
  isLight: boolean;
};

function parseHex(color: string): [number, number, number] | null {
  const raw = color.trim();
  const hex = raw.startsWith("#") ? raw.slice(1) : raw;
  if (!/^[0-9a-fA-F]{6}$/.test(hex)) return null;
  return [
    Number.parseInt(hex.slice(0, 2), 16),
    Number.parseInt(hex.slice(2, 4), 16),
    Number.parseInt(hex.slice(4, 6), 16),
  ];
}

/** WCAG 相对亮度；无法解析时按深色处理（宁可多给底板）。 */
export function relativeLuminance(color: string): number {
  const rgb = parseHex(color);
  if (!rgb) return 0;
  const [r, g, b] = rgb.map(channel => {
    const value = channel / 255;
    return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrastRatio(first: string, second: string): number {
  const a = relativeLuminance(first);
  const b = relativeLuminance(second);
  const [high, low] = a > b ? [a, b] : [b, a];
  return (high + 0.05) / (low + 0.05);
}

function mixToward(color: string, target: string, amount: number): string {
  const from = parseHex(color);
  const to = parseHex(target);
  if (!from || !to) return color;
  const mixed = from.map((channel, index) =>
    Math.round(channel + ((to[index] as number) - channel) * amount),
  );
  return `#${mixed.map(channel => channel.toString(16).padStart(2, "0")).join("")}`;
}

/**
 * 把强调色调到在给定底色上可读（目标 ≥4.5:1）。
 * 白色外观因此得到一个深灰文字色，而不是在白底上消失。
 */
export function readableOn(color: string, background: string): string {
  if (contrastRatio(color, background) >= 4.5) return color;
  const toward = relativeLuminance(background) > 0.5 ? "#000000" : "#ffffff";
  let result = color;
  for (let step = 1; step <= 20; step += 1) {
    result = mixToward(color, toward, step / 20);
    if (contrastRatio(result, background) >= 4.5) return result;
  }
  return result;
}

/** 一套 appearance token：所有渲染点共用，不各自打补丁。 */
export function botAppearanceTokens(color?: string | null): BotAppearanceTokens {
  const parsed = color ? parseHex(color) : null;
  const accent = parsed
    ? `#${color!.trim().replace("#", "").toLowerCase()}`
    : DEFAULT_BOT_ACCENT;
  const isLight = relativeLuminance(accent) > 0.5;
  return {
    accent,
    isLight,
    surface: isLight ? NEUTRAL_SURFACE : "transparent",
    ink: isLight ? NEUTRAL_INK : "rgba(255, 255, 255, 0.92)",
    text: readableOn(accent, "#ffffff"),
    textOnDark: readableOn(accent, "#101010"),
    // 白底白字的气泡等于没有气泡：浅色本体换成中性底板 + 深墨文字。
    bubble: isLight ? "var(--kai-bot-neutral-bubble)" : accent,
    bubbleInk: isLight ? "var(--kai-bot-neutral-bubble-ink)" : "#ffffff",
  };
}

/** 把 token 铺成 CSS 变量；容器只需挂这一个 style。 */
export function botAppearanceVars(color?: string | null): Record<string, string> {
  const tokens = botAppearanceTokens(color);
  return {
    "--kai-bot-accent": tokens.accent,
    "--kai-bot-surface": tokens.surface,
    "--kai-bot-ink": tokens.ink,
    "--kai-bot-text": tokens.text,
    "--kai-bot-text-on-dark": tokens.textOnDark,
    "--kai-bot-bubble": tokens.bubble,
    "--kai-bot-bubble-ink": tokens.bubbleInk,
  };
}
