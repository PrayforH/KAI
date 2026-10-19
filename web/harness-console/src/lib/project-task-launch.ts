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

const launching = new Set<string>();

/** Do not discard the hand-off when a composer is still loading or send is rejected. */
export async function tryProjectTaskLaunch(
  userId: string, threadId: string, send: () => Promise<boolean>,
): Promise<void> {
  const id = key(userId, threadId);
  let armed = pending.has(id);
  try { armed = sessionStorage.getItem(id) === "1" || armed; } catch { /* memory fallback */ }
  if (!armed || launching.has(id)) return;
  launching.add(id);
  try {
    if (await send()) consumeProjectTaskLaunch(userId, threadId);
  } finally { launching.delete(id); }
}
