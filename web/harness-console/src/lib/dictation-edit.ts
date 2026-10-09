export type DictationPatch = { text: string; start: number; end: number; length: number };

function continuation(previous: string, current: string): string | undefined {
  if (current.startsWith(previous)) return current.slice(previous.length);
  // A shorter interim result must not resurrect text that was deliberately removed.
  if (previous.startsWith(current)) return;
  const normalize = (text: string) => Array.from(text).map(char => char.toLowerCase())
    .join("").replace(/[^\p{L}\p{N}]/gu, "");
  const before = normalize(previous);
  const positions: number[] = []; let after = "";
  let offset = 0;
  for (const char of current) {
    const folded = normalize(char);
    after += folded;
    for (let index = 0; index < folded.length; index++) positions.push(offset + char.length);
    offset += char.length;
  }
  // Match the end of the already displayed source through punctuation revisions.
  for (let length = Math.min(before.length, after.length); length >= 3; length--) {
    const anchor = before.slice(-length), at = after.indexOf(anchor);
    if (at >= 0 && at === after.lastIndexOf(anchor)) return current.slice(positions[at + length - 1]);
  }
}

/** Own the new dictated span; manual edits protect old text while future speech continues. */
export class DictationEdit {
  private snapshot: string;
  private start: number;
  private end: number;
  private original: string;
  private manual = false;
  private source = "";
  private draft = "";

  constructor(text: string, start: number, end: number) {
    this.snapshot = text;
    this.start = start; this.end = end;
    this.original = text.slice(start, end);
  }

  private reconcile(text: string) {
    if (text === this.snapshot) return;
    let left = 0;
    while (left < text.length && left < this.snapshot.length && text[left] === this.snapshot[left]) left++;
    let right = this.snapshot.length, nextRight = text.length;
    while (right > left && nextRight > left && this.snapshot[right - 1] === text[nextRight - 1]) { right--; nextRight--; }
    if (right <= this.start) {
      const shift = nextRight - right;
      this.start += shift; this.end += shift;
    } else if (left < this.end) {
      // Start a fresh insertion at the user's edit. Keep the ASR source cursor
      // so future cumulative results cannot bring deleted words back.
      this.start = this.end = nextRight;
      this.original = this.draft = "";
      this.manual = true;
    }
    this.snapshot = text;
  }

  observe(text: string) { this.reconcile(text); }

  replace(text: string, speech: string, refined = false): DictationPatch | undefined {
    this.reconcile(text);
    if (this.manual) {
      if (refined) return;
      const added = continuation(this.source, speech);
      // Rebase a changed source to let subsequent growth resume. A shorter
      // hypothesis retains its previous cursor until it catches up.
      if (!this.source.startsWith(speech)) this.source = speech;
      if (added === undefined) return;
      const fresh = !this.draft && (this.start === 0 || text[this.start - 1] === "\n")
        ? added.replace(/^[\s，,。.!！?？;；:：、]+/u, "") : added;
      this.draft += fresh;
      speech = this.draft;
    } else {
      this.source = this.draft = speech;
    }
    return this.patch(text, speech);
  }

  private patch(text: string, speech: string): DictationPatch {
    const previous = text.slice(this.start, this.end);
    let left = 0;
    while (left < previous.length && left < speech.length && previous[left] === speech[left]) left++;
    let right = previous.length, nextRight = speech.length;
    while (right > left && nextRight > left && previous[right - 1] === speech[nextRight - 1]) { right--; nextRight--; }
    const patch = { text: text.slice(0, this.start) + speech + text.slice(this.end),
      start: this.start + left, end: this.start + right, length: nextRight - left };
    this.end = this.start + speech.length;
    this.snapshot = patch.text;
    return patch;
  }

  cancel(text: string) { this.reconcile(text); return this.patch(text, this.original); }
}

export function dictationCaret(position: number, patch: DictationPatch) {
  if (position < patch.start) return position;
  if (position > patch.end) return position + patch.length - (patch.end - patch.start);
  return patch.start + patch.length;
}
