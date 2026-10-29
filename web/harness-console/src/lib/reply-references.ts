/**
 * Reply references, keyed by the message that quotes an original.
 *
 * The server history endpoint is the source of truth (`replies` map per page);
 * this store only bridges fetched pages to message rendering and holds the
 * optimistic reference of the message that was just sent. The client excerpt
 * is display-only and never routes anything.
 */

export interface ReplyReference {
  messageId: string;
  excerpt: string | null;
}

export interface ReplyLookup {
  reply: ReplyReference;
  unavailable: boolean;
  optimistic: boolean;
}

type Listener = () => void;

const references = new Map<string, ReadonlyMap<string, ReplyReference>>();
const pending = new Map<string, ReplyReference>();
const unavailable = new Set<string>();
const listeners = new Set<Listener>();
let version = 0;

function emit() {
  version += 1;
  for (const listener of listeners) listener();
}

export function subscribeReplyReferences(listener: Listener) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getReplyStoreVersion() {
  return version;
}

export function getReplyStoreServerVersion() {
  return 0;
}

export function setThreadReplyReferences(
  threadId: string,
  entries: Record<string, { messageId?: string; excerpt?: string | null }> | undefined | null,
) {
  if (!entries) return;
  const incoming = Object.entries(entries).filter(
    (entry): entry is [string, { messageId: string; excerpt?: string }] =>
      typeof entry[1]?.messageId === "string" && entry[1].messageId.length > 0,
  );
  if (!incoming.length) return;
  const next = new Map(references.get(threadId) ?? []);
  for (const [key, value] of incoming) {
    next.set(key, { messageId: value.messageId, excerpt: value.excerpt ?? null });
  }
  references.set(threadId, next);
  // The server now owns the reference the optimistic message was showing.
  pending.delete(threadId);
  emit();
}

export function setPendingReply(threadId: string, reply: ReplyReference | null) {
  if (reply) pending.set(threadId, reply);
  else pending.delete(threadId);
  emit();
}

export function markReplyUnavailable(threadId: string, messageId: string) {
  unavailable.add(`${threadId}\u0000${messageId}`);
  emit();
}

/** Pure lookup: which reply header (if any) renders on this message. */
export function lookupReply(
  threadId: string,
  messageId: string,
  isLatestUserMessage: boolean,
): ReplyLookup | null {
  const stored = references.get(threadId)?.get(messageId);
  if (stored) {
    return {
      reply: stored,
      unavailable: unavailable.has(`${threadId}\u0000${stored.messageId}`),
      optimistic: false,
    };
  }
  if (isLatestUserMessage) {
    const optimistic = pending.get(threadId);
    if (optimistic) return { reply: optimistic, unavailable: false, optimistic: true };
  }
  return null;
}

/** Locate the quoted original: scroll to it, or fetch its context by id. */
export async function locateReplyOriginal(
  threadId: string,
  reference: ReplyReference,
): Promise<
  | { kind: "revealed" }
  | { kind: "original"; excerpt: string | null; createdAt: string | null }
  | { kind: "unavailable" }
> {
  const selector = `[data-turn-id="${CSS.escape(reference.messageId)}"], [data-turn-answer="${CSS.escape(reference.messageId)}"]`;
  const node = document.querySelector(selector);
  if (node instanceof HTMLElement) {
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    node.focus({ preventScroll: true });
    return { kind: "revealed" };
  }
  try {
    const response = await fetch(
      `/api/agui/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(reference.messageId)}`,
      { cache: "no-store" },
    );
    if (!response.ok) return { kind: "unavailable" };
    const body = (await response.json()) as {
      found?: boolean;
      excerpt?: string | null;
      createdAt?: string | null;
    };
    if (!body.found) return { kind: "unavailable" };
    return {
      kind: "original",
      excerpt: body.excerpt ?? null,
      createdAt: body.createdAt ?? null,
    };
  } catch {
    return { kind: "unavailable" };
  }
}
