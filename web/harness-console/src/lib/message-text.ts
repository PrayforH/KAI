const TRAILING_INVISIBLE_SPACE =
  /[\t \u00a0\u1680\u2000-\u200b\u202f\u205f\u3000\ufeff]+$/g;

/**
 * Keep Markdown paragraph boundaries while removing transport noise that can
 * turn a response into several visually empty rows. This is shared by the
 * renderer, copy action and message editor so live and restored messages use
 * the same text shape.
 */
export function normalizeMessageText(value: string): string {
  return value
    .replace(/\r\n?/g, "\n")
    .replace(/[\u2028\u2029]/g, "\n")
    .split("\n")
    .map((line) => line.replace(TRAILING_INVISIBLE_SPACE, ""))
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
