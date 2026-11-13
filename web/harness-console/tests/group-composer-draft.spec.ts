import { describe, expect, it } from "vitest";

import {
  groupComposerDraftKey,
  loadGroupComposerDraft,
  persistGroupComposerDraft,
} from "../src/lib/group-composer-draft";

function memoryStorage() {
  const map = new Map<string, string>();
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
    removeItem: (key: string) => void map.delete(key),
  };
}

describe("group composer drafts", () => {
  it("keeps a draft per user and group", () => {
    const storage = memoryStorage();
    persistGroupComposerDraft(storage, "u1", "g1", "群一的草稿");
    persistGroupComposerDraft(storage, "u1", "g2", "群二的草稿");
    persistGroupComposerDraft(storage, "u2", "g1", "另一个人的草稿");

    expect(loadGroupComposerDraft(storage, "u1", "g1")).toBe("群一的草稿");
    expect(loadGroupComposerDraft(storage, "u1", "g2")).toBe("群二的草稿");
    expect(loadGroupComposerDraft(storage, "u2", "g1")).toBe("另一个人的草稿");
    expect(loadGroupComposerDraft(storage, "u1", "g3")).toBe("");
  });

  it("clears the draft when the text is emptied", () => {
    const storage = memoryStorage();
    persistGroupComposerDraft(storage, "u1", "g1", "草稿");
    persistGroupComposerDraft(storage, "u1", "g1", "");
    expect(storage.getItem(groupComposerDraftKey("u1", "g1"))).toBeNull();
  });

  it("never throws when browser storage is blocked", () => {
    const blocked = {
      getItem: () => { throw new Error("blocked"); },
      setItem: () => { throw new Error("blocked"); },
      removeItem: () => { throw new Error("blocked"); },
    };
    expect(loadGroupComposerDraft(blocked, "u1", "g1")).toBe("");
    expect(() => persistGroupComposerDraft(blocked, "u1", "g1", "x")).not.toThrow();
  });
});
