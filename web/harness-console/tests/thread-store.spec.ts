import { describe, expect, it } from "vitest";
import {
  bindThreadAgent,
  createUserScopedStorage,
  createNewThread,
  loadOrCreateThread,
  loadThreadAgent,
  isLocalThread,
  markThreadPersisted,
} from "../src/lib/thread-store";

function memoryStorage(initial?: string) {
  const values = new Map<string, string>();
  if (initial) values.set("harness-console-thread", initial);
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, next: string) => {
      values.set(key, next);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
    value: () => values.get("harness-console-thread") ?? null,
  };
}

describe("thread store", () => {
  it("isolates the active task and Agent binding for each signed-in user", () => {
    const storage = memoryStorage();
    const firstUser = createUserScopedStorage(storage, "user-1");
    const secondUser = createUserScopedStorage(storage, "user-2");

    const firstThread = createNewThread(firstUser, () => "thread-user-1");
    bindThreadAgent(firstUser, firstThread, {
      name: "public-opinion-agent",
      version: "0.3.11",
      ownerUserId: "user-1",
    });

    expect(loadOrCreateThread(secondUser, () => "thread-user-2")).toBe(
      "thread-user-2",
    );
    expect(loadThreadAgent(secondUser, firstThread)).toBeNull();
    expect(loadThreadAgent(firstUser, firstThread)?.ownerUserId).toBe("user-1");
  });

  it("marks only new local threads and clears the marker after server creation", () => {
    const storage = memoryStorage("old-thread");
    expect(isLocalThread(storage, "old-thread")).toBe(false);
    const id = createNewThread(storage, () => "local-thread");
    expect(isLocalThread(storage, id)).toBe(true);
    expect(isLocalThread(storage, loadOrCreateThread(storage))).toBe(true);
    markThreadPersisted(storage, id);
    expect(isLocalThread(storage, id)).toBe(false);
  });

  it("restores the existing thread after refresh", () => {
    const storage = memoryStorage("thread-existing");

    expect(loadOrCreateThread(storage, () => "thread-new")).toBe("thread-existing");
    expect(storage.value()).toBe("thread-existing");
  });

  it("creates and persists a thread when none exists", () => {
    const storage = memoryStorage();

    expect(loadOrCreateThread(storage, () => "thread-created")).toBe("thread-created");
    expect(storage.value()).toBe("thread-created");
  });

  it("replaces the current thread only for a new conversation", () => {
    const storage = memoryStorage("thread-existing");

    expect(createNewThread(storage, () => "thread-replacement")).toBe(
      "thread-replacement",
    );
    expect(storage.value()).toBe("thread-replacement");
  });

  it("binds an immutable agent coordinate to each thread", () => {
    const storage = memoryStorage("thread-existing");

    bindThreadAgent(storage, "thread-existing", {
      name: "public-opinion-agent",
      version: "0.2.0",
      displayName: "舆情研判 Agent",
      domain: "public-opinion",
    });

    expect(loadThreadAgent(storage, "thread-existing")).toEqual({
      name: "public-opinion-agent",
      version: "0.2.0",
      displayName: "舆情研判 Agent",
      domain: "public-opinion",
    });
    expect(loadThreadAgent(storage, "another-thread")).toBeNull();
  });
});
