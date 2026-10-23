// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { useProjectTaskLaunch } from "../src/components/use-project-task-launch";
import { armProjectTaskLaunch } from "../src/lib/project-task-launch";

const scope = {userId: "launch-test", threadId: "delayed-runtime"};
afterEach(() => vi.unstubAllGlobals());
it("waits for runtime readiness, tolerates a remount and never resends an accepted launch", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const values = new Map<string, string>();
  const storage = {getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)};
  vi.stubGlobal("localStorage", storage); vi.stubGlobal("sessionStorage", storage);
  armProjectTaskLaunch(scope.userId, scope.threadId, "加载完成后执行");
  const send = vi.fn(async () => true);
  function Launch({ready}: {ready: boolean}) {
    useProjectTaskLaunch(scope, ready, () => send());
    return null;
  }
  const root = createRoot(document.createElement("div"));
  await act(async () => root.render(<StrictMode><Launch ready={false} /></StrictMode>));
  expect(send).not.toHaveBeenCalled();
  await act(async () => root.render(<StrictMode><Launch ready /></StrictMode>));
  expect(send).toHaveBeenCalledTimes(1);
  await act(async () => root.unmount());
  const remounted = createRoot(document.createElement("div"));
  await act(async () => remounted.render(<StrictMode><Launch ready /></StrictMode>));
  expect(send).toHaveBeenCalledTimes(1);
  await act(async () => remounted.unmount());
});
