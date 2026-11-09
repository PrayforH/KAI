import { PERSONAL_ASSISTANT_THREAD } from "../components/personal-assistant-context";

/** Task coordinates never inherit a specialist or the assistant page's identity. */
export function taskThreadHref(threadId: string): string {
  return `/?${new URLSearchParams({ thread: threadId })}`;
}

const TASK_LAUNCH_PARAMS = [
  "project", "tab", "agent", "version", "space", "owner",
  "skill", "skillScope", "agentDraft", "agentLabel",
];

/** Keep old result links usable while giving each conversation one page owner. */
export function canonicalConversationHref(assistantEntry: boolean, search: URLSearchParams): string | null {
  const thread = search.get("thread");
  if (assistantEntry && !search.get("bot") && thread && thread !== PERSONAL_ASSISTANT_THREAD) {
    const next = new URLSearchParams(search);
    next.delete("bot");
    next.delete("computer");
    return `/?${next}`;
  }
  if (!assistantEntry && thread !== PERSONAL_ASSISTANT_THREAD) return null;

  const next = new URLSearchParams(search);
  next.delete("thread");
  if (!assistantEntry) next.delete("bot");
  for (const key of TASK_LAUNCH_PARAMS) next.delete(key);
  if (assistantEntry && next.toString() === search.toString()) return null;
  return `/assistant${next.size ? `?${next}` : ""}`;
}
