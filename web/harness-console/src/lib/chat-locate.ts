/**
 * 在聊天中定位：先找已经挂载的消息；找不到时让会话往回翻页再试。
 * 历史面板能读到完整历史，但 assistant-ui 只挂载已导入的消息，
 * 老消息必须先通过会话自己的 loadEarlier 导入，滚动才有落点。
 */
import {
  beginLocate, LOCATE_LANDED_EVENT, settleLocate,
} from "./locate-signal";
import type { LoadEarlierResult, LocateLoadResult } from "./task-history";

type Locator = {
  threadId: string;
  /**
   * False while the thread's first page has not seeded the pagination window.
   * Until then `hasMore` is ignorance, not "the thread ends here".
   */
  ready?: () => boolean;
  hasMore: () => boolean;
  /** Loads up to `pages` earlier pages and imports them once. */
  loadEarlier: (pages?: number) => Promise<LoadEarlierResult | boolean>;
  /**
   * Id-driven walk: pages back until the target id is seen, then imports once.
   * Real conversation locators provide this; the batch path below stays for
   * callers (and tests) that only have `loadEarlier`.
   */
  loadUntil?: (messageId: string, options?: { onProgress?: () => void }) => Promise<LocateLoadResult>;
  /** Resolves when another reader's in-flight page request settles. */
  waitForLoading?: () => Promise<void>;
  /** The conversation frame, so 定位 never matches another thread's DOM. */
  frame?: () => HTMLElement | null;
};

/** Why a locate finished; "retry" is not proof the message is absent. */
export type LocateOutcome = "located" | "missing" | "retry";

let locator: Locator | null = null;

/** pages per legacy locate step; one step is one full repository import. */
const LOCATE_PAGE_BATCH = 3;
/**
 * How long one import may take to mount, or one seed/busy reader may take to
 * settle. This is a render wait after a known hit, never a blind wait after a
 * page that did not contain the target.
 */
const LOCATE_WAIT_MS = 1_200;

/** Identical clicks share one walk instead of paging the thread twice. */
const inFlightLocates = new Map<string, Promise<LocateOutcome>>();

/** The ring stays on the landed message for the prototype's 1.8s. */
const LOCATED_CLASS = "assistant-history-located";
/**
 * Two variants of the same bloom animation. Restarting a CSS animation
 * normally needs a forced reflow (`void node.offsetWidth`), which on a
 * 500-turn thread reads layout for the whole document right when the jump
 * lands. Alternating the animation name restarts it without that read.
 */
const LOCATED_VARIANTS = [
  "assistant-history-located-a",
  "assistant-history-located-b",
] as const;
const LOCATED_MS = 1_800;
/** A long jump is a cut; a short opacity fade keeps it from reading as a freeze. */
const VIEWPORT_FADE_MS = 180;
const NEARBY_DISTANCE_PX = 600;
/** A fallback viewport height when the frame has not been laid out yet. */
const FALLBACK_VIEWPORT_PX = 600;

const locateTimers = new WeakMap<HTMLElement, ReturnType<typeof setTimeout>>();
let locateVariant = 0;

export function registerHistoryLocator(value: Locator | null): void {
  locator = value;
}

function raf(): Promise<void> {
  if (typeof requestAnimationFrame !== "function") return Promise.resolve();
  return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

/** Flash the message when it is already mounted in this locator's frame. */
function flashMounted(active: Locator | null, messageId: string): LocateOutcome | null {
  const target = rootFor(active);
  if (!target || !findMessage(messageId, target)) return null;
  return flashMessage(messageId, target) ? "located" : "retry";
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

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined"
    && typeof window.matchMedia === "function"
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Ring the landed message. The ring is a pseudo-element whose only animated
 * properties are opacity and transform: the old version animated box-shadow
 * and background on a whole message block, repainting it every frame for 1.8s
 * and dropping frames on long threads.
 */
function markLocated(target: HTMLElement): void {
  const previous = locateTimers.get(target);
  if (previous) clearTimeout(previous);
  target.classList.remove(LOCATED_CLASS, ...LOCATED_VARIANTS);
  const variant = LOCATED_VARIANTS[locateVariant % LOCATED_VARIANTS.length];
  locateVariant += 1;
  target.classList.add(LOCATED_CLASS, variant);
  const timer = setTimeout(() => {
    target.classList.remove(LOCATED_CLASS, variant);
    locateTimers.delete(target);
  }, LOCATED_MS);
  locateTimers.set(target, timer);
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
  const reduced = prefersReducedMotion();
  // A long jump is laid out in one frame; smooth is for nearby hops only, where
  // the animation is the feedback rather than a slow crawl across the thread.
  const nearby = distance <= Math.max(NEARBY_DISTANCE_PX, (viewport?.clientHeight ?? FALLBACK_VIEWPORT_PX) * 2);
  target.scrollIntoView?.({ block: "start", behavior: !reduced && nearby ? "smooth" : "auto" });
  // The far jump has no spatial continuity; a short fade-in on the viewport
  // softens the cut without delaying the landing. Opacity only, so the thread
  // is not repainted message by message.
  if (!reduced && !nearby && viewport && typeof viewport.animate === "function") {
    viewport.animate(
      [{ opacity: 0.55 }, { opacity: 1 }],
      { duration: VIEWPORT_FADE_MS, easing: "ease-out" },
    );
  }
  markLocated(target);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(LOCATE_LANDED_EVENT, { detail: { messageId } }));
  }
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
async function waitForRoot(root: () => ParentNode | null): Promise<ParentNode | null> {
  const deadline = Date.now() + LOCATE_WAIT_MS;
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

/**
 * A locate can start before the thread's first page has seeded `hasMore`.
 * Wait for a seeded locator for the same thread; a reading that never arrives
 * is a retry, never proof the message is absent.
 */
async function waitForSeededLocator(threadId: string | undefined): Promise<Locator | null> {
  const waitUntil = Date.now() + LOCATE_WAIT_MS;
  for (;;) {
    const candidate = locator;
    // A newer locator re-registers between renders; keep watching through the
    // brief window where the effect has torn the old one down.
    if (candidate && threadId && candidate.threadId !== threadId) return null;
    if (candidate && (!candidate.ready || candidate.ready())) return candidate;
    if (Date.now() >= waitUntil) return null;
    await raf();
  }
}

/** Wait for another reader's page, but never longer than one mount window. */
async function waitForBusyLoad(active: Locator): Promise<void> {
  if (!active.waitForLoading) return;
  await new Promise<void>((resolve) => {
    const timer = globalThis.setTimeout(resolve, LOCATE_WAIT_MS);
    const settle = () => {
      globalThis.clearTimeout(timer);
      resolve();
    };
    active.waitForLoading!().then(settle, settle);
  });
}

export function locateInChat(
  messageId: string,
  threadId?: string,
  options: { onProgress?: () => void; maxPages?: number } = {},
): Promise<LocateOutcome> {
  // Double-clicking 在聊天中定位 must not start two walks over the same thread.
  const key = `${threadId ?? ""} ${messageId}`;
  const pending = inFlightLocates.get(key);
  if (pending) return pending;
  // The rail stops measuring turns while this walk imports and lands; it
  // scans once afterwards instead of competing with the scroll.
  const generation = beginLocate(threadId);
  const run = walkToMessage(messageId, threadId, options).then(
    (outcome) => {
      settleLocate(threadId, generation, outcome === "located");
      return outcome;
    },
    (cause: unknown) => {
      settleLocate(threadId, generation, false);
      throw cause;
    },
  ).finally(() => {
    if (inFlightLocates.get(key) === run) inFlightLocates.delete(key);
  });
  inFlightLocates.set(key, run);
  return run;
}

async function walkToMessage(
  messageId: string,
  threadId: string | undefined,
  options: { onProgress?: () => void; maxPages?: number },
): Promise<LocateOutcome> {
  // Check the thread before touching the DOM: a locator for another thread can
  // only expose a stranger's frame, and its "not here" is not "not anywhere".
  if (locator && threadId && locator.threadId !== threadId) return "retry";
  // A message that is already on screen needs no paging and no seeded window.
  const alreadyMounted = flashMounted(locator, messageId);
  if (alreadyMounted) return alreadyMounted;

  // Capture the locator for the whole walk: switching threads mid-locate must
  // not keep paging the new thread on behalf of the old one.
  let active = locator;
  // Without a registered thread nothing can be paged, and absence is unproven.
  if (!active) return "retry";
  if (threadId && active.threadId !== threadId) return "retry";
  if (active.ready && !active.ready()) {
    // Cold open: the first page has not seeded `hasMore` yet. Treating that as
    // "no earlier pages" reported a false miss within a millisecond.
    const seeded = await waitForSeededLocator(threadId);
    if (!seeded) return "retry";
    active = seeded;
    const adopted = flashMounted(active, messageId);
    if (adopted) return adopted;
  }

  const root = () => rootFor(active);
  if (!root()) {
    // The registered frame is remounting. Wait briefly for it instead of
    // paging a thread the view cannot show yet; still never scan the document.
    const appeared = await waitForRoot(root);
    if (!appeared) return "retry";
    if (findMessage(messageId, appeared)) {
      return flashMessage(messageId, appeared) ? "located" : "retry";
    }
  }

  if (active.loadUntil) return locateById(active, messageId, root, options);
  return locateByBatches(active, messageId, root, options);
}

/**
 * Id-driven walk: no fixed page budget and no timer between pages. Absence is
 * only reported after the thread itself says there are no earlier pages.
 */
async function locateById(
  active: Locator,
  messageId: string,
  root: () => ParentNode | null,
  options: { onProgress?: () => void },
): Promise<LocateOutcome> {
  let outcome = await active.loadUntil!(messageId, { onProgress: options.onProgress });
  if (locator !== active) return "retry";
  if (outcome === "busy") {
    // Another reader (scroll-to-top, the index rail) already has a page in
    // flight. Wait for it, then look immediately and resume the walk.
    await waitForBusyLoad(active);
    if (locator !== active) return "retry";
    const mountedNow = flashMounted(active, messageId);
    if (mountedNow) return mountedNow;
    outcome = await active.loadUntil!(messageId, { onProgress: options.onProgress });
    if (locator !== active) return "retry";
  }
  if (outcome === "missing") return "missing";
  if (outcome === "found") {
    // The import lands on a later frame; this wait starts after a known hit.
    if (await waitForMessage(messageId, root, Date.now() + LOCATE_WAIT_MS)) {
      return flashMounted(active, messageId) ?? "retry";
    }
    return "retry";
  }
  // "failed" (or busy twice) says nothing about whether the message exists.
  return "retry";
}

/**
 * Legacy batch walk for locators without `loadUntil`. Keeps `maxPages` only as
 * an explicit caller guard-rail; running out is ignorance, not absence.
 */
async function locateByBatches(
  active: Locator,
  messageId: string,
  root: () => ParentNode | null,
  options: { onProgress?: () => void; maxPages?: number },
): Promise<LocateOutcome> {
  const maxPages = options.maxPages ?? Number.POSITIVE_INFINITY;
  /** Wait for a fresh mount after a page that may contain the target. */
  const mountDeadline = () => Date.now() + LOCATE_WAIT_MS;
  let loadedPages = 0;
  let waitedForBusy = false;
  /** True only when the thread itself said there are no earlier pages. */
  let reachedEnd = false;
  while (loadedPages < maxPages) {
    if (locator !== active) return "retry";
    if (!active.hasMore()) { reachedEnd = true; break; }
    const outcome = normalizeResult(
      await active.loadEarlier(Math.min(LOCATE_PAGE_BATCH, maxPages - loadedPages)),
    );
    if (locator !== active) return "retry";
    if (outcome === "busy") {
      // Another reader already has a page in flight. Wait for it, then look
      // immediately — waiting a full mount window before asking again was the
      // stall readers felt.
      if (waitedForBusy) return "retry";
      waitedForBusy = true;
      await waitForBusyLoad(active);
      if (locator !== active) return "retry";
      const mountedNow = flashMounted(active, messageId);
      if (mountedNow) return mountedNow;
      // That settle may have been the thread's last page, and assistant-ui only
      // renders a freshly imported repository on later frames. Read `hasMore`
      // right away and the message looks absent before it is ever drawn.
      if (!active.hasMore() && await waitForMessage(messageId, root, mountDeadline())) {
        return flashMounted(active, messageId) ?? "retry";
      }
      continue;
    }
    if (outcome === "exhausted") { reachedEnd = true; break; }
    if (outcome === "failed") {
      // A transport error says nothing about whether the message exists.
      return flashMounted(active, messageId) ?? "retry";
    }
    loadedPages += LOCATE_PAGE_BATCH;
    options.onProgress?.();
    if (await waitForMessage(messageId, root, mountDeadline())) {
      return flashMounted(active, messageId) ?? "retry";
    }
  }
  if (locator !== active) return "retry";
  // A caller guard-rail that ran out is ignorance, not absence: only a
  // confirmed end of the thread may report "missing".
  if (!reachedEnd) return "retry";
  return flashMounted(active, messageId) ?? "missing";
}
