export type DictationPatch = { text: string; start: number; end: number; length: number };

/** Own only the dictated span; edits inside it take precedence over later ASR/LLM results. */
export class DictationEdit {
  private snapshot: string;
  private start: number;
  private end: number;
  private original: string;
  private detached = false;

  constructor(text: string, start: number, end: number) {
    this.snapshot = text;
    this.start = start; this.end = end;
    this.original = text.slice(start, end);
  }

  private reconcile(text: string) {
    if (text === this.snapshot || this.detached) return;
    let left = 0;
    while (left < text.length && left < this.snapshot.length && text[left] === this.snapshot[left]) left++;
    let right = this.snapshot.length, nextRight = text.length;
    while (right > left && nextRight > left && this.snapshot[right - 1] === text[nextRight - 1]) { right--; nextRight--; }
    if (right <= this.start) {
      const shift = nextRight - right;
      this.start += shift; this.end += shift;
    } else if (left < this.end) {
      this.detached = true;
    }
    this.snapshot = text;
  }

  observe(text: string) { this.reconcile(text); }

  replace(text: string, speech: string): DictationPatch | undefined {
    this.reconcile(text);
    if (this.detached) return;
    const patch = { text: text.slice(0, this.start) + speech + text.slice(this.end), start: this.start, end: this.end, length: speech.length };
    this.end = this.start + speech.length;
    this.snapshot = patch.text;
    return patch;
  }

  cancel(text: string) { return this.replace(text, this.original); }
}

export function dictationCaret(position: number, patch: DictationPatch) {
  if (position < patch.start) return position;
  if (position > patch.end) return position + patch.length - (patch.end - patch.start);
  return patch.start + patch.length;
}
