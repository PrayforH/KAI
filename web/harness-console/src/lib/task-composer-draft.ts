export interface ComposerDraftStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

const COMPOSER_DRAFT_PREFIX = "harness-task-composer-draft:v1:";
const MAX_COMPOSER_DRAFT_CHARS = 100_000;

export function taskComposerDraftKey(userId: string, threadId: string) {
  return `${COMPOSER_DRAFT_PREFIX}${encodeURIComponent(userId)}:${encodeURIComponent(threadId)}`;
}

export function loadTaskComposerDraft(
  storage: ComposerDraftStorage,
  userId: string,
  threadId: string,
) {
  try {
    const value = storage.getItem(taskComposerDraftKey(userId, threadId));
    return value?.slice(0, MAX_COMPOSER_DRAFT_CHARS) ?? "";
  } catch {
    return "";
  }
}

export function persistTaskComposerDraft(
  storage: ComposerDraftStorage,
  userId: string,
  threadId: string,
  text: string,
) {
  const key = taskComposerDraftKey(userId, threadId);
  try {
    if (!text) {
      storage.removeItem(key);
      return;
    }
    storage.setItem(key, text.slice(0, MAX_COMPOSER_DRAFT_CHARS));
  } catch {
    // Local persistence is a convenience. A blocked/quota-full browser store
    // must never prevent the user from composing or sending a task.
  }
}
