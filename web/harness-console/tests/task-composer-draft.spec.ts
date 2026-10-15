import { describe, expect, it } from "vitest";
import {
  loadTaskComposerDraft,
  pendingNewTaskDraft,
  persistTaskComposerDraft,
  taskComposerDraftKey,
  type ComposerDraftStorage,
} from "../src/lib/task-composer-draft";

function memoryStorage(): ComposerDraftStorage & { values: Map<string, string> } {
  const values = new Map<string, string>();
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
  };
}

describe("task composer draft persistence", () => {
  it("isolates unsent text by user and thread", () => {
    const storage = memoryStorage();
    persistTaskComposerDraft(storage, "user/a", "thread:1", "待补充的任务说明");

    expect(loadTaskComposerDraft(storage, "user/a", "thread:1")).toBe("待补充的任务说明");
    expect(loadTaskComposerDraft(storage, "user/b", "thread:1")).toBe("");
    expect(loadTaskComposerDraft(storage, "user/a", "thread:2")).toBe("");
    expect(taskComposerDraftKey("user/a", "thread:1")).toContain("user%2Fa:thread%3A1");
  });

  it("removes the stored value after the composer is cleared", () => {
    const storage = memoryStorage();
    persistTaskComposerDraft(storage, "user-1", "thread-1", "未发送");
    persistTaskComposerDraft(storage, "user-1", "thread-1", "");

    expect(storage.values.size).toBe(0);
  });

  it("never lets unavailable browser storage break composing", () => {
    const unavailable: ComposerDraftStorage = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("quota"); },
      removeItem: () => { throw new Error("blocked"); },
    };

    expect(loadTaskComposerDraft(unavailable, "user-1", "thread-1")).toBe("");
    expect(() => persistTaskComposerDraft(unavailable, "user-1", "thread-1", "text")).not.toThrow();
  });
});


it("recovers a legacy orphaned new-task draft without restoring a historical task's draft", () => {
  const storage = memoryStorage();
  persistTaskComposerDraft(storage, "legacy-user", "orphan", "之前没发送的文字");
  persistTaskComposerDraft(storage, "legacy-user", "history", "历史任务的跟进");
  const enumerable = { ...storage, length: storage.values.size,
    key: (index: number) => [...storage.values.keys()][index] ?? null };
  expect(pendingNewTaskDraft(enumerable, "legacy-user", new Set(["history"]))).toBe("orphan");
});
