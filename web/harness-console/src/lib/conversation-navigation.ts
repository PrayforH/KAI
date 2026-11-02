import { PERSONAL_ASSISTANT_THREAD } from "../components/personal-assistant-context";

/** Task coordinates never inherit a specialist or the assistant page's identity. */
export function taskThreadHref(threadId: string): string {
  return `/?${new URLSearchParams({ thread: threadId })}`;
}

/** Keep old result links usable while giving each conversation one page owner. */
export function canonicalConversationHref(assistantEntry: boolean, search: URLSearchParams): string | null {
  const thread = search.get("thread");
  const bot = assistantEntry ? search.get("bot") : null;
  if (assistantEntry && bot && thread) {
    const next = new URLSearchParams(search);
    next.delete("thread");
    return `/assistant?${next}`;
  }
  if (!thread) return null;
  if (assistantEntry && thread !== PERSONAL_ASSISTANT_THREAD) {
    const next = new URLSearchParams(search);
    next.delete("bot");
    next.delete("computer");
    return `/?${next}`;
  }
  if (thread === PERSONAL_ASSISTANT_THREAD) {
    const next = new URLSearchParams(search);
    next.delete("thread");
    next.delete("bot");
    return `/assistant${next.size ? `?${next}` : ""}`;
  }
  return null;
}
