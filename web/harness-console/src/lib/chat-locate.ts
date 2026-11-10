/**
 * 在聊天中定位：先找已经挂载的消息；找不到时让会话往回翻页再试。
 * 历史面板能读到完整历史，但 assistant-ui 只挂载已导入的几页消息，
 * 老消息必须先通过会话自己的 loadEarlier 导入，滚动才有落点。
 */
type Locator = {
  threadId: string;
  hasMore: () => boolean;
  loadEarlier: () => Promise<void>;
};

let locator: Locator | null = null;

export function registerHistoryLocator(value: Locator | null): void {
  locator = value;
}

function nextFrames(): Promise<void> {
  if (typeof requestAnimationFrame !== "function") return Promise.resolve();
  return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

/** Scroll to the message and flash it, like the prototype's paLocateInChat. */
export function flashMessage(messageId: string, root: ParentNode | null = typeof document === "undefined" ? null : document): boolean {
  const target = root?.querySelector<HTMLElement>(`[data-message-id="${messageId}"]`);
  if (!target) return false;
  target.scrollIntoView?.({ block: "center", behavior: "smooth" });
  target.classList.remove("assistant-history-located");
  void target.offsetWidth;
  target.classList.add("assistant-history-located");
  window.setTimeout(() => target.classList.remove("assistant-history-located"), 1800);
  return true;
}

export async function locateInChat(messageId: string, threadId?: string, maxPages = 5): Promise<boolean> {
  if (flashMessage(messageId)) return true;
  if (!locator || (threadId && locator.threadId !== threadId)) return false;
  for (let page = 0; page < maxPages; page += 1) {
    if (!locator.hasMore()) return false;
    await locator.loadEarlier();
    await nextFrames();
    if (flashMessage(messageId)) return true;
  }
  return false;
}
