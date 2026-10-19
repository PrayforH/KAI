import { afterEach, expect, it, vi } from "vitest";
import {
  armProjectTaskLaunch,
  consumeProjectTaskLaunch,
  tryProjectTaskLaunch,
} from "../src/lib/project-task-launch";
import { loadTaskComposerSnapshot } from "../src/lib/task-composer-draft";

afterEach(() => vi.unstubAllGlobals());
it("carries the entered requirement to the shared composer and consumes launch exactly once per user/thread", () => {
  const values = new Map<string, string>();
  const storage = {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
  vi.stubGlobal("localStorage", storage);
  vi.stubGlobal("sessionStorage", storage);
  armProjectTaskLaunch("owner", "thread-1", "整理经营数据");
  expect(loadTaskComposerSnapshot(storage, "owner", "thread-1").text).toBe(
    "整理经营数据",
  );
  expect(consumeProjectTaskLaunch("other", "thread-1")).toBe(false);
  expect(consumeProjectTaskLaunch("owner", "other-thread")).toBe(false);
  expect(consumeProjectTaskLaunch("owner", "thread-1")).toBe(true);
  expect(consumeProjectTaskLaunch("owner", "thread-1")).toBe(false);
  expect(loadTaskComposerSnapshot(storage, "owner", "thread-1").text).toBe(
    "整理经营数据",
  );
});

it("retains a blocked launch and accepts it exactly once after the composer becomes ready", async () => {
  const storage = {getItem: () => null, setItem: () => {}, removeItem: () => {}};
  vi.stubGlobal("localStorage", storage); vi.stubGlobal("sessionStorage", storage);
  armProjectTaskLaunch("owner", "slow-launch", "开始执行");
  const blocked = vi.fn(async () => false);
  await tryProjectTaskLaunch("owner", "slow-launch", blocked);
  let accepted!: (value: boolean) => void;
  const send = vi.fn(() => new Promise<boolean>(resolve => { accepted = resolve; }));
  const sending = tryProjectTaskLaunch("owner", "slow-launch", send);
  await tryProjectTaskLaunch("owner", "slow-launch", send);
  expect(send).toHaveBeenCalledTimes(1);
  accepted(true); await sending;
  await tryProjectTaskLaunch("owner", "slow-launch", send);
  expect(send).toHaveBeenCalledTimes(1);
  expect(consumeProjectTaskLaunch("owner", "slow-launch")).toBe(false);
});
