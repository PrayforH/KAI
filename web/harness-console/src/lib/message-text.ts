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

const REPEATED_BLOCK_FLOOR = 40;

/**
 * Stream/tool-result merge occasionally appends the same answer again, or a
 * second copy of its opening, after the first one. Collapse only a suffix that
 * is byte-identical to a prefix of the same reply and starts on a line
 * boundary — long enough that it cannot be ordinary repeated prose.
 */
export function collapseRepeatedAssistantText(value: string): string {
  const text = normalizeMessageText(value);
  if (text.length < REPEATED_BLOCK_FLOOR * 2) return text;
  for (let index = 1; index < text.length; index += 1) {
    if (text[index - 1] !== "\n") continue;
    const tail = text.slice(index).trim();
    if (tail.length < REPEATED_BLOCK_FLOOR) continue;
    if (tail.length > text.length - REPEATED_BLOCK_FLOOR) continue;
    if (!text.startsWith(tail)) continue;
    return text.slice(0, index).trim();
  }
  return text;
}

/** Adjacent assistant turns with identical text are one duplicated reply. */
export function isDuplicateAssistantReply(
  current: string,
  previous: string,
  status?: string,
): boolean {
  if (status === "running") return false;
  const text = normalizeMessageText(current);
  return Boolean(text) && text === normalizeMessageText(previous);
}

/** Temperature range separators are prose, not GFM strikethrough delimiters.
 * Preserve literal code (including an unfinished streaming code span/fence).
 */
export function normalizeTemperatureRanges(value: string): string {
  let fence: { char: string; length: number } | undefined;
  let ticks = 0;
  return value.split("\n").map((line) => {
    const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line);
    if (fence) {
      if (marker && marker[1][0] === fence.char && marker[1].length >= fence.length && !marker[2].trim()) fence = undefined;
      return line;
    }
    if (marker && !ticks) {
      fence = { char: marker[1][0], length: marker[1].length };
      return line;
    }
    if (/^(?: {4}|\t)/.test(line)) return line;
    return line.split(/(`+)/).map((part, index) => {
      if (index % 2) {
        ticks = ticks === part.length ? 0 : ticks || part.length;
        return part;
      }
      if (ticks) return part;
      return part.replace(
        /(?<![\d~])([+-]?\d+(?:\.\d+)?(?:[ \t]*(?:℃|°[CF]))?)[ \t]*~{1,2}(?!~)[ \t]*([+-]?\d+(?:\.\d+)?[ \t]*(?:℃|°[CF]))/g,
        "$1～$2",
      );
    }).join("");
  }).join("\n");
}

/** Provider punctuation fragments are not progress updates; preserve final answers. */
export function isProgressNoise(value: string): boolean {
  const text = normalizeMessageText(value);
  return !text || (text.length <= 8 && /^[\p{P}\p{Z}\s]+$/u.test(text));
}
