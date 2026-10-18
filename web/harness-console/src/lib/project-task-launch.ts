import { persistTaskComposerSnapshot } from "./task-composer-draft";
const pending = new Set<string>();
const key = (userId: string, threadId: string) =>
  `harness:project-launch:${encodeURIComponent(userId)}:${encodeURIComponent(threadId)}`;
export function armProjectTaskLaunch(
  userId: string,
  threadId: string,
  prompt: string,
) {
  persistTaskComposerSnapshot(localStorage, userId, threadId, prompt, []);
  const id = key(userId, threadId);
  pending.add(id);
  try {
    sessionStorage.setItem(id, "1");
  } catch {
    /* in-memory launch still works */
  }
}
export function consumeProjectTaskLaunch(userId: string, threadId: string) {
  const id = key(userId, threadId);
  let armed = pending.delete(id);
  try {
    armed = sessionStorage.getItem(id) === "1" || armed;
    sessionStorage.removeItem(id);
  } catch {
    /* memory fallback */
  }
  return armed;
}
