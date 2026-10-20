// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { startProjectTaskTransition, waitForProjectTaskTransition } from "../src/lib/project-task-transition";
import { armProjectTaskLaunch } from "../src/lib/project-task-launch";
import { useProjectTaskLaunch } from "../src/components/use-project-task-launch";

beforeEach(() => {
  const values = new Map<string, string>();
  const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) };
  vi.stubGlobal("localStorage", storage); vi.stubGlobal("sessionStorage", storage);
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); document.body.innerHTML = ""; });
function mockTransition() {
  let finish!: () => void;
  const finished = new Promise<void>(resolve => { finish = resolve; });
  const skip = vi.fn(finish);
  const start = vi.fn((update: () => Promise<void>) => { void update(); return { finished, skipTransition: skip }; });
  vi.stubGlobal("document", Object.assign(document, { startViewTransition: start }));
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: false })));
  return { start, finish, skip };
}
it("waits for the destination composer and animation before sending exactly once", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const transition = mockTransition();
  const scope = { userId: "transition-user", threadId: "transition-task" };
  armProjectTaskLaunch(scope.userId, scope.threadId, "验证动画");
  document.body.innerHTML = '<aside class="task-sidebar"></aside><div data-project-resources></div><div data-project-launch-composer><form class="aui-composer-root"></form></div>';
  startProjectTaskTransition(scope.threadId, () => {
    document.body.innerHTML = '<aside class="task-sidebar"></aside><div class="task-content-shell"><div class="harness-composer-shell"><form class="aui-composer-root"></form></div></div>';
  });
  const send = vi.fn(async () => true);
  function Target({ ready }: { ready: boolean }) { useProjectTaskLaunch(scope, ready, send); return null; }
  const root = createRoot(document.createElement("div"));
  await act(async () => root.render(<Target ready={false} />));
  expect(send).not.toHaveBeenCalled();
  await act(async () => root.render(<Target ready />));
  expect(send).not.toHaveBeenCalled();
  expect(document.querySelector<HTMLElement>("form")!.style.viewTransitionName).toBe("project-composer");
  // Repeated readiness must not leave names behind or trigger another send.
  void waitForProjectTaskTransition(scope.threadId);
  await act(async () => transition.finish());
  expect(send).toHaveBeenCalledTimes(1);
  expect(document.querySelector<HTMLElement>("form")!.style.viewTransitionName).toBe("");
  await act(async () => root.unmount());
});
it("does not send after the destination was unmounted during animation", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const transition = mockTransition();
  const scope = { userId: "transition-user", threadId: "left-task" };
  armProjectTaskLaunch(scope.userId, scope.threadId, "不要误发");
  startProjectTaskTransition(scope.threadId, () => {});
  const send = vi.fn(async () => true);
  function Target() { useProjectTaskLaunch(scope, true, send); return null; }
  const root = createRoot(document.createElement("div"));
  await act(async () => root.render(<Target />));
  await act(async () => root.unmount());
  await act(async () => transition.finish());
  expect(send).not.toHaveBeenCalled();
});
it("releases a stalled snapshot and falls back for reduced motion", async () => {
  vi.useFakeTimers(); const transition = mockTransition(); const navigate = vi.fn();
  startProjectTaskTransition("slow", navigate);
  await vi.advanceTimersByTimeAsync(3500);
  expect(transition.skip).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledOnce();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  startProjectTaskTransition("reduced", navigate);
  expect(transition.start).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledTimes(2);
});
