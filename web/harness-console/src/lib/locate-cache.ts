/**
 * Messages the chat-history drawer already fetched, kept so 「在聊天中定位」
 * does not page the same runs in a second time.
 *
 * This is a cache of plain message arrays, not a second source of truth: it
 * never issues requests, it is cleared whenever the thread's history is
 * invalidated, and both its axes are bounded (threads and messages) so a long
 * session cannot grow it without limit.
 */

export type CachedMessage = { id: string };

type CachedThread = {
  /** Newest page first, matching the order the history endpoint returns them. */
  pages: CachedMessage[][];
  count: number;
  receivedAt: number;
};

export const LOCATE_CACHE_TTL_MS = 5 * 60_000;
export const LOCATE_CACHE_THREADS = 4;
export const LOCATE_CACHE_MESSAGES = 4_000;

const cache = new Map<string, CachedThread>();

function touch(threadId: string, entry: CachedThread): void {
  cache.delete(threadId);
  cache.set(threadId, entry);
  while (cache.size > LOCATE_CACHE_THREADS) {
    const oldest = cache.keys().next().value;
    if (typeof oldest !== "string") break;
    cache.delete(oldest);
  }
}

/** Record one fetched page (page order: newest first). */
export function rememberThreadMessages<T extends CachedMessage>(
  threadId: string,
  messages: readonly T[],
): void {
  if (messages.length === 0) return;
  const current = cache.get(threadId);
  const fresh = !current || Date.now() - current.receivedAt > LOCATE_CACHE_TTL_MS;
  // A copy, so later mutation of the caller's page cannot rewrite cached data.
  const page = Array.from(messages, message => message as CachedMessage);
  const entry: CachedThread = fresh
    ? { pages: [page], count: page.length, receivedAt: Date.now() }
    : {
        pages: [...current.pages, page],
        count: current.count + page.length,
        receivedAt: Date.now(),
      };
  // Pages arrive newest first, so the oldest data sits at the end of the list.
  // Drop whole pages from there; one page is always kept so a single oversized
  // page cannot make the entry unusable.
  while (entry.count > LOCATE_CACHE_MESSAGES && entry.pages.length > 1) {
    const dropped = entry.pages.pop();
    if (!dropped) break;
    entry.count -= dropped.length;
  }
  touch(threadId, entry);
}

/**
 * Every cached message, oldest first, when the requested message is among them.
 * `null` means "not cached" — it says nothing about whether the message exists.
 */
export function readLocatedMessages<T extends CachedMessage>(
  threadId: string,
  messageId: string,
): T[] | null {
  const entry = cache.get(threadId);
  if (!entry) return null;
  if (Date.now() - entry.receivedAt > LOCATE_CACHE_TTL_MS) {
    cache.delete(threadId);
    return null;
  }
  if (!entry.pages.some(page => page.some(message => message.id === messageId))) return null;
  touch(threadId, entry);
  const seen = new Set<string>();
  const messages: CachedMessage[] = [];
  for (const page of entry.pages.slice().reverse()) {
    for (const message of page) {
      if (seen.has(message.id)) continue;
      seen.add(message.id);
      messages.push(message);
    }
  }
  return messages as T[];
}

export function clearLocatedMessages(threadId?: string): void {
  if (threadId) cache.delete(threadId);
  else cache.clear();
}

/** Test/observation hook: how many threads and messages are held right now. */
export function locatedCacheSize(): { threads: number; messages: number } {
  let messages = 0;
  for (const entry of cache.values()) messages += entry.count;
  return { threads: cache.size, messages };
}
