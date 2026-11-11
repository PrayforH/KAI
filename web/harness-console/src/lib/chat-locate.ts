/**
 * 在聊天中定位：先找已经挂载的消息；找不到时让会话往回翻页再试。
 * 历史面板能读到完整历史，但 assistant-ui 只挂载已导入的消息，
 * 老消息必须先通过会话自己的 loadEarlier 导入，滚动才有落点。
 */
type Locator = {
  threadId: string;
  hasMore: () => boolean;
  /** Loads up to `pages` earlier pages and imports them once. */
  loadEarlier: (pages?: number) => Promise<boolean>;
  /** The conversation frame, so 定位 never matches another thread's DOM. */
  frame?: () => HTMLElement | null;
};

let locator: Locator | null = null;

/** pages per locate step; one step is one full repository import. */
const LOCATE_PAGE_BATCH = 3;
/** A guard-rail, not the old 5-page ceiling: the thread itself ends earlier. */
const LOCATE_MAX_PAGES = 21;
const LOCATE_WAIT_MS = 500;
const LOCATE_DEADLINE_MS = 15_000;

export function registerHistoryLocator(value: Locator | null): void {
  locator = value;
}

function raf(): Promise<void> {
  if (typeof requestAnimationFrame !== "function") return Promise.resolve();
  return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

function selectorFor(messageId: string): string {
  const escaped = typeof CSS !== "undefined" && typeof CSS.escape === "function"
    ? CSS.escape(messageId)
    : messageId.replace(/["\\]/g, "\\$&");
  return `[data-message-id="${escaped}"], [data-turn-id="${escaped}"]`;
}

function findMessage(messageId: string, root: ParentNode): HTMLElement | null {
  // The chat viewport is the only place a message can be a scroll target.
  const scope = root.querySelector<HTMLElement>(".aui-thread-viewport") ?? root;
  const node = scope.querySelector(selectorFor(messageId))
    ?? root.querySelector(selectorFor(messageId));
  return node instanceof HTMLElement ? node : null;
}

/** Scroll to the message and flash it, like the prototype's paLocateInChat. */
export function flashMessage(messageId: string, root: ParentNode | null = typeof document === "undefined" ? null : document): boolean {
  if (!root) return false;
  const target = findMessage(messageId, root);
  if (!target) return false;
  const viewport = target.closest<HTMLElement>(".aui-thread-viewport");
  const distance = Math.abs(
    target.getBoundingClientRect().top - (viewport?.getBoundingClientRect().top ?? 0),
  );
  // A long jump is laid out in one frame; smooth is for nearby hops only, where
  // the animation is the feedback rather than a slow crawl across the thread.
  const nearby = distance <= Math.max(600, (viewport?.clientHeight ?? 600) * 2);
  target.scrollIntoView?.({ block: "start", behavior: nearby ? "smooth" : "auto" });
  target.classList.remove("assistant-history-located");
  void target.offsetWidth;
  target.classList.add("assistant-history-located");
  window.setTimeout(() => target.classList.remove("assistant-history-located"), 1800);
  return true;
}

/**
 * After an import the DOM is not ready in a fixed number of frames: a big
 * merged repository can render across several. Poll for the node instead.
 */
async function waitForMessage(messageId: string, root: () => ParentNode | null): Promise<boolean> {
  const deadline = Date.now() + LOCATE_WAIT_MS;
  for (;;) {
    const target = root();
    if (target && findMessage(messageId, target)) return true;
    if (Date.now() >= deadline) return false;
    await raf();
  }
}

export async function locateInChat(
  messageId: string,
  threadId?: string,
  options: { onProgress?: () => void; maxPages?: number } = {},
): Promise<boolean> {
  const root = () => locator?.frame?.() ?? (typeof document === "undefined" ? null : document);
  const current = root();
  if (current && findMessage(messageId, current)) return flashMessage(messageId, current);
  if (!locator || (threadId && locator.threadId !== threadId)) return false;

  const maxPages = options.maxPages ?? LOCATE_MAX_PAGES;
  const deadline = Date.now() + LOCATE_DEADLINE_MS;
  let loadedPages = 0;
  while (loadedPages < maxPages && Date.now() < deadline) {
    if (!locator.hasMore()) break;
    const loaded = await locator.loadEarlier(Math.min(LOCATE_PAGE_BATCH, maxPages - loadedPages));
    if (!loaded) break;
    loadedPages += LOCATE_PAGE_BATCH;
    options.onProgress?.();
    if (await waitForMessage(messageId, root)) {
      return flashMessage(messageId, root());
    }
  }
  const target = root();
  return target ? flashMessage(messageId, target) : false;
}
