import type { ComposerDraftStorage } from "./task-composer-draft";

const GROUP_DRAFT_PREFIX = "harness-group-composer-draft:v1:";
const MAX_GROUP_DRAFT_CHARS = 100_000;

/** 草稿按用户 + 群隔离；切群再切回来、刷新页面都不丢。 */
export function groupComposerDraftKey(userId: string, groupId: string) {
  return `${GROUP_DRAFT_PREFIX}${encodeURIComponent(userId)}:${encodeURIComponent(groupId)}`;
}

export function loadGroupComposerDraft(
  storage: ComposerDraftStorage,
  userId: string,
  groupId: string,
): string {
  try {
    return storage.getItem(groupComposerDraftKey(userId, groupId))?.slice(0, MAX_GROUP_DRAFT_CHARS) ?? "";
  } catch {
    return "";
  }
}

export function persistGroupComposerDraft(
  storage: ComposerDraftStorage,
  userId: string,
  groupId: string,
  text: string,
): void {
  try {
    const key = groupComposerDraftKey(userId, groupId);
    if (!text) storage.removeItem(key);
    else storage.setItem(key, text.slice(0, MAX_GROUP_DRAFT_CHARS));
  } catch {
    // 浏览器禁用存储时草稿只在内存里保留，不能挡住发送。
  }
}
