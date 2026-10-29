import { describe, expect, it } from "vitest";
import {
  lookupReply,
  markReplyUnavailable,
  setPendingReply,
  setThreadReplyReferences,
} from "./reply-references";

const THREAD = "thread-reply";
// The store is module-global; every test uses its own thread key.
let threadCounter = 0;
function freshThread() {
  threadCounter += 1;
  return `${THREAD}-${threadCounter}`;
}

describe("reply references", () => {
  it("renders the stored reference and clears the optimistic one once the server owns it", () => {
    const thread = freshThread();
    setPendingReply(thread, { messageId: "user-a", excerpt: "原文" });
    expect(lookupReply(thread, "user-b", true)).toEqual({
      reply: { messageId: "user-a", excerpt: "原文" },
      unavailable: false,
      optimistic: true,
    });
    // An older message never shows the optimistic reference.
    expect(lookupReply(thread, "user-c", false)).toBeNull();

    setThreadReplyReferences(thread, { "user-b": { messageId: "user-a", excerpt: "原文" } });
    expect(lookupReply(thread, "user-b", true)?.optimistic).toBe(false);
    expect(lookupReply(thread, "user-b", false)).toEqual({
      reply: { messageId: "user-a", excerpt: "原文" },
      unavailable: false,
      optimistic: false,
    });
  });

  it("degrades an unresolvable original instead of re-pointing it", () => {
    const thread = freshThread();
    setThreadReplyReferences(thread, { "user-b": { messageId: "user-a", excerpt: null } });
    expect(lookupReply(thread, "user-b", false)?.unavailable).toBe(false);
    markReplyUnavailable(thread, "user-a");
    expect(lookupReply(thread, "user-b", false)?.unavailable).toBe(true);
    // The unavailability is scoped to the quoted original, not the quoting message.
    expect(lookupReply(thread, "user-b", true)?.reply.messageId).toBe("user-a");
  });

  it("ignores malformed server entries", () => {
    const thread = freshThread();
    setThreadReplyReferences(thread, { "user-b": { excerpt: "no id" } });
    expect(lookupReply(thread, "user-b", false)).toBeNull();
  });
});
