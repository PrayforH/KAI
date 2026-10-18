import { createNewThread, isLocalThread, selectThread, type ThreadStorage } from "./thread-store";

const PROJECT_DRAFT = "harness:project-draft:";
const DRAFT_PROJECT = "harness:draft-project:";

/** User-scoped, local drafts only. Opening a project never creates a server task. */
export function projectDraft(storage: ThreadStorage, projectId: string, fresh = false): string {
  const previous = storage.getItem(PROJECT_DRAFT + projectId);
  if (!fresh && previous && isLocalThread(storage, previous)) return selectThread(storage, previous);
  const threadId = createNewThread(storage);
  storage.setItem(PROJECT_DRAFT + projectId, threadId);
  storage.setItem(DRAFT_PROJECT + threadId, projectId);
  return threadId;
}

export function draftProject(storage: ThreadStorage, threadId: string): string | null {
  return isLocalThread(storage, threadId) ? storage.getItem(DRAFT_PROJECT + threadId) : null;
}
