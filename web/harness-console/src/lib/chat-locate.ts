/**
 * 在聊天中定位：先找已经挂载的消息；找不到时让会话往回翻页再试。
 * 历史面板能读到完整历史，但 assistant-ui 只挂载已导入的消息，
 * 老消息必须先通过会话自己的 loadEarlier 导入，滚动才有落点。
 */
import type { LoadEarlierResult } from "./task-history";

type Locator = {
  threadId: string;
  hasMore: () => boolean;
  /** Loads up to `pages` earlier pages and imports them once. */
  loadEarlier: (pages?: number) => Promise<LoadEarlierResult | boolean>;
  /** Resolves when another reader's in-flight page request settles. */
  waitForLoading?: () => Promise<void>;
  /** The conversation frame, so 定位 never matches another thread's DOM. */
  frame?: () => HTMLElement | null;
};

/** Why a locate finished; "retry" is not proof the message is absent. */
export type LocateOutcome = "located" | "missing" | "retry";

let locator: Locator | null = null;

/** pages per locate step; one step is one full repository import. */
const LOCATE_PAGE_BATCH = 3;
/** A guard-rail, not the old 5-page ceiling: the thread itself ends earlier. */
const LOCATE_MAX_PAGES = 21;
/**
 * How long one import may take to mount. Long threads render across several
 * frames, and jsdom-style fixed frame counts cannot judge that. Each wait is
 * clamped to the remaining walk deadline below, so a slow page cannot push a
 * locate past LOCATE_DEADLINE_MS and report a false "missing".
 */
const LOCATE_WAIT_MS = 1_200;
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
async function waitForMessage(
  messageId: string,
  root: () => ParentNode | null,
  deadline = Date.now() + LOCATE_WAIT_MS,
): Promise<boolean> {
  for (;;) {
    const target = root();
    if (target && findMessage(messageId, target)) return true;
    if (Date.now() >= deadline) return false;
    await raf();
  }
}

/** Wait for a registered frame to exist again; null means it stayed gone. */
async function waitForRoot(
  root: () => ParentNode | null,
  budgetMs = LOCATE_WAIT_MS,
): Promise<ParentNode | null> {
  const deadline = Date.now() + budgetMs;
  for (;;) {
    const node = root();
    if (node) return node;
    if (Date.now() >= deadline) return null;
    await raf();
  }
}

/** The frame that actually rendered this locator's thread. */
function rootFor(registered: Locator | null): ParentNode | null {
  if (typeof document === "undefined") return null;
  // A registered locator with a frame callback must never fall back to the
  // whole document: that would match another thread that happens to be mounted.
  if (registered) return registered.frame ? registered.frame() : document;
  return document;
}

function normalizeResult(value: LoadEarlierResult | boolean): LoadEarlierResult {
  if (value === true) return "loaded";
  if (value === false) return "exhausted";
  return value;
}

export async function locateInChat(
  messageId: string,
  threadId?: string,
  options: { onProgress?: () => void; maxPages?: number } = {},
): Promise<LocateOutcome> {
  // Capture the locator for the whole walk: switching threads mid-locate must
  // not keep paging the new thread on behalf of the old one.
  const active = locator;
  const root = () => rootFor(active);
  const deadline = Date.now() + LOCATE_DEADLINE_MS;
  const current = root();
  if (current && findMessage(messageId, current)) {
    return flashMessage(messageId, current) ? "located" : "missing";
  }
  if (!active || (threadId && active.threadId !== threadId)) return "missing";
  if (!current) {
    // The registered frame is remounting. Wait briefly for it instead of
    // paging a thread the view cannot show yet; still never scan the document.
    const appeared = await waitForRoot(root, Math.min(LOCATE_WAIT_MS, LOCATE_DEADLINE_MS));
    if (!appeared) return "retry";
    if (findMessage(messageId, appeared)) {
      return flashMessage(messageId, appeared) ? "located" : "retry";
    }
  }

  const maxPages = options.maxPages ?? LOCATE_MAX_PAGES;
  /** Wait for a fresh mount, but never overshoot the walk's own deadline. */
  const mountDeadline = () => Math.min(Date.now() + LOCATE_WAIT_MS, deadline);
  let loadedPages = 0;
  let waitedForBusy = false;
  while (loadedPages < maxPages && Date.now() < deadline) {
    if (locator !== active) return "retry";
    if (!active.hasMore()) break;
    const outcome = normalizeResult(
      await active.loadEarlier(Math.min(LOCATE_PAGE_BATCH, maxPages - loadedPages)),
    );
    if (locator !== active) return "retry";
    if (outcome === "busy") {
      // Another reader (scroll-to-top, the index rail) already has a page in
      // flight. Wait for it, then retry once; never report "missing" for busy.
      if (waitedForBusy) return "retry";
      waitedForBusy = true;
      await active.waitForLoading?.();
      if (locator !== active) return "retry";
      if (await waitForMessage(messageId, root, mountDeadline())) {
        return flashMessage(messageId, root()) ? "located" : "retry";
      }
      continue;
    }
    if (outcome === "exhausted") break;
    if (outcome === "failed") {
      // A transport error says nothing about whether the message exists.
      const target = root();
      return target && findMessage(messageId, target) ? "located" : "retry";
    }
    loadedPages += LOCATE_PAGE_BATCH;
    options.onProgress?.();
    if (await waitForMessage(messageId, root, mountDeadline())) {
      return flashMessage(messageId, root()) ? "located" : "retry";
    }
  }
  if (locator !== active) return "retry";
  const target = root();
  if (!target) return "retry";
  return flashMessage(messageId, target) ? "located" : "missing";
}
