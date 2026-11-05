import { BOT_SHAPES } from "../lib/bot-appearance";
/** Same identity in the rail, conversation header, source line and appearance picker. */
export function BotGlyph({shape, color}: {shape?: string | null; color: string}) {
  const body = BOT_SHAPES.find(item => item.id === shape) ?? BOT_SHAPES[0];
  return <svg viewBox="0 0 64 64" focusable="false" aria-hidden="true">
    <path d={body.path} fill={color} />
    {/* 眼睛颜色来自统一 appearance token：浅色本体用深墨，深色本体用白。 */}
    <g fill="var(--kai-bot-ink, var(--bot-eye-color, #151515))" transform="rotate(-20 32 30)">
      <rect x="24" y="24" width="5" height="12" rx="2.5" />
      <rect x="35" y="24" width="5" height="12" rx="2.5" />
    </g>
  </svg>;
}
