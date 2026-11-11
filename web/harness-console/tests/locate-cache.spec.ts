import { afterEach, describe, expect, it } from "vitest";
import {
  LOCATE_CACHE_MESSAGES,
  LOCATE_CACHE_THREADS,
  clearLocatedMessages,
  locatedCacheSize,
  readLocatedMessages,
  rememberThreadMessages,
} from "../src/lib/locate-cache";

const page = (from: number, to: number) => Array.from(
  { length: to - from + 1 },
  (_, index) => ({ id: `m-${from + index}` }),
);

afterEach(() => clearLocatedMessages());

describe("locate message cache", () => {
  it("returns messages oldest-first when the target was fetched", () => {
    // Pages arrive newest first, exactly as the history endpoint serves them.
    rememberThreadMessages("thread-a", page(10, 19));
    rememberThreadMessages("thread-a", page(0, 9));

    const messages = readLocatedMessages<{ id: string }>("thread-a", "m-3");
    expect(messages?.map(message => message.id))
      .toEqual(Array.from({ length: 20 }, (_, index) => `m-${index}`));
  });

  it("does not claim a message that was never fetched", () => {
    rememberThreadMessages("thread-a", page(10, 19));
    expect(readLocatedMessages("thread-a", "m-3")).toBeNull();
    expect(readLocatedMessages("thread-unknown", "m-10")).toBeNull();
  });

  it("drops whole oldest pages once the message budget is exceeded", () => {
    const pageSize = 100;
    const pages = Math.ceil(LOCATE_CACHE_MESSAGES / pageSize) + 2;
    // Newest page first, so the last recorded page holds the oldest ids.
    for (let index = 0; index < pages; index += 1) {
      const newest = index * pageSize;
      rememberThreadMessages("thread-a", page(newest, newest + pageSize - 1));
    }

    const size = locatedCacheSize();
    expect(size.messages).toBeLessThanOrEqual(LOCATE_CACHE_MESSAGES + pageSize);
    // Pages are recorded newest first, so the last page is the oldest data:
    // eviction drops it first while the newest page stays usable.
    expect(readLocatedMessages("thread-a", "m-0")).not.toBeNull();
    expect(readLocatedMessages("thread-a", `m-${(pages - 1) * pageSize}`)).toBeNull();
  }, 20_000);

  it("keeps a bounded number of threads", () => {
    for (let index = 0; index < LOCATE_CACHE_THREADS + 2; index += 1) {
      rememberThreadMessages(`thread-${index}`, page(0, 4));
    }
    expect(locatedCacheSize().threads).toBe(LOCATE_CACHE_THREADS);
    expect(readLocatedMessages("thread-0", "m-0")).toBeNull();
    expect(readLocatedMessages(`thread-${LOCATE_CACHE_THREADS + 1}`, "m-0")).not.toBeNull();
  });

  it("clears one thread or all of them", () => {
    rememberThreadMessages("thread-a", page(0, 4));
    rememberThreadMessages("thread-b", page(0, 4));

    clearLocatedMessages("thread-a");
    expect(readLocatedMessages("thread-a", "m-0")).toBeNull();
    expect(readLocatedMessages("thread-b", "m-0")).not.toBeNull();

    clearLocatedMessages();
    expect(locatedCacheSize()).toEqual({ threads: 0, messages: 0 });
  });
});
