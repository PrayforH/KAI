import type { Attachment, CreateAttachment } from "@assistant-ui/react";
import { inputArtifactIdFromAttachment } from "./input-attachment-adapter";

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


export interface TaskComposerSnapshot {
  text: string;
  attachments: Array<CreateAttachment | File>;
}
const snapshots = new Map<string, TaskComposerSnapshot>();
const snapshotKey = (userId: string, threadId: string) => taskComposerDraftKey(userId, threadId) + ":attachments";
const newDraftKey = (userId: string) => `harness-new-task-draft:${encodeURIComponent(userId)}`;

export function rememberNewTaskDraft(storage: ComposerDraftStorage, userId: string, threadId: string) {
  try { storage.setItem(newDraftKey(userId), threadId); } catch { /* browser storage unavailable */ }
}

export function pendingNewTaskDraft(storage: ComposerDraftStorage & { length?: number; key?: (index: number) => string | null }, userId: string, durableThreads: ReadonlySet<string> = new Set()): string | null {
  try {
    const candidates: string[] = [];
    const remembered = storage.getItem(newDraftKey(userId));
    if (remembered) candidates.push(remembered);
    // Recover a draft orphaned by the previous version, while excluding real history tasks.
    const prefix = `${COMPOSER_DRAFT_PREFIX}${encodeURIComponent(userId)}:`;
    if (storage.key) for (let index = (storage.length ?? 0) - 1; index >= 0; index--) {
      const key = storage.key(index);
      if (key?.startsWith(prefix) && !key.endsWith(":attachments")) candidates.push(decodeURIComponent(key.slice(prefix.length)));
    }
    for (const threadId of new Set(candidates)) {
      if (durableThreads.has(threadId)) continue;
      const draft = loadTaskComposerSnapshot(storage, userId, threadId);
      if (draft.text || draft.attachments.length) return threadId;
    }
    return null;
  } catch { return null; }
}

export function persistTaskComposerSnapshot(storage: ComposerDraftStorage, userId: string, threadId: string, text: string, attachments: readonly Attachment[]) {
  const saved: Array<CreateAttachment | File> = [];
  const durable: CreateAttachment[] = [];
  for (const attachment of attachments) {
    const artifactId = inputArtifactIdFromAttachment(attachment);
    if (artifactId) {
      const item: CreateAttachment = { id: artifactId, type: attachment.type, name: attachment.name, contentType: attachment.contentType,
        content: [{ type: "file", data: artifactId, mimeType: attachment.contentType ?? "application/octet-stream", filename: attachment.name }] };
      saved.push(item); durable.push(item);
    } else if ("file" in attachment && attachment.file instanceof File) saved.push(attachment.file);
  }
  const key = snapshotKey(userId, threadId);
  snapshots.set(key, { text, attachments: saved });
  persistTaskComposerDraft(storage, userId, threadId, text);
  try {
    if (durable.length) storage.setItem(key, JSON.stringify(durable));
    else storage.removeItem(key);
  } catch { /* retain the in-memory draft */ }
}

export function loadTaskComposerSnapshot(storage: ComposerDraftStorage, userId: string, threadId: string): TaskComposerSnapshot {
  const key = snapshotKey(userId, threadId);
  const memory = snapshots.get(key);
  if (memory) return memory;
  let attachments: CreateAttachment[] = [];
  try {
    const raw: unknown = JSON.parse(storage.getItem(key) ?? "[]");
    if (Array.isArray(raw)) attachments = raw.filter((item): item is CreateAttachment => {
      if (!item || typeof item !== "object") return false;
      return typeof item.id === "string" && item.id.startsWith("input_artifact_") && typeof item.name === "string"
        && ["image", "document", "file"].includes(item.type) && Array.isArray(item.content);
    });
  } catch { /* old or unavailable browser storage */ }
  return { text: loadTaskComposerDraft(storage, userId, threadId), attachments };
}
