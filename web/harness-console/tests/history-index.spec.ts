import { afterEach, expect, it, vi } from "vitest";
import {
  HISTORY_INDEX_TTL_MS,
  clearHistoryIndex,
  readHistoryIndex,
  writeHistoryIndex,
  type HistoryIndex,
} from "../src/lib/history-index";

function index(id: string): HistoryIndex {
  return { entries: [{ id, role: "user", text: id, files: [], searchText: id }], files: [] };
}

afterEach(() => {
  clearHistoryIndex();
  vi.useRealTimers();
});

it("reuses an index inside the TTL", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-11T00:00:00Z"));
  writeHistoryIndex("thread_1", index("a"));
  vi.setSystemTime(new Date(Date.parse("2026-11-11T00:00:00Z") + HISTORY_INDEX_TTL_MS));
  expect(readHistoryIndex("thread_1")?.entries[0].id).toBe("a");
});

it("drops an index once the TTL has passed", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-11T00:00:00Z"));
  writeHistoryIndex("thread_1", index("a"));
  vi.setSystemTime(new Date(Date.parse("2026-11-11T00:00:00Z") + HISTORY_INDEX_TTL_MS + 1));
  expect(readHistoryIndex("thread_1")).toBeNull();
});

it("invalidates one thread without touching the others", () => {
  writeHistoryIndex("thread_1", index("a"));
  writeHistoryIndex("thread_2", index("b"));
  clearHistoryIndex("thread_1");
  expect(readHistoryIndex("thread_1")).toBeNull();
  expect(readHistoryIndex("thread_2")?.entries[0].id).toBe("b");
});
