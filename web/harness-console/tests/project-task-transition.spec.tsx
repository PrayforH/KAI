// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { startProjectTaskTransition, revealProjectTaskTransition } from "../src/lib/project-task-transition";
import { armProjectTaskLaunch } from "../src/lib/project-task-launch";
import { useProjectTaskLaunch } from "../src/components/use-project-task-launch";

beforeEach(() => {
  vi.useFakeTimers();
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
  const paint = () => vi.advanceTimersByTimeAsync(0);
  return { start, finish, skip, paint };
}
it("starts once when the destination is ready, without waiting for animation to finish", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const transition = mockTransition();
  const scope = { userId: "transition-user", threadId: "transition-task" };
  armProjectTaskLaunch(scope.userId, scope.threadId, "验证动画");
  document.body.innerHTML = '<aside class="task-sidebar"></aside><div data-project-resources></div><div data-project-launch-composer><form class="aui-composer-root"></form></div>';
  startProjectTaskTransition(scope.threadId, () => {
    document.body.innerHTML = '<aside class="task-sidebar"></aside><div class="task-content-shell"><div class="harness-composer-shell"><form class="aui-composer-root"></form></div></div>';
  });
  const send = vi.fn(async () => true);
  function Target({ ready, snapshotReady = false }: { ready: boolean; snapshotReady?: boolean }) { useProjectTaskLaunch(scope, ready, send, snapshotReady); return null; }
  const root = createRoot(document.createElement("div"));
  await act(async () => root.render(<Target ready={false} />));
  expect(send).not.toHaveBeenCalled();
  await act(async () => root.render(<Target ready />));
  expect(send).toHaveBeenCalledTimes(1);
  await act(async () => transition.paint());
  expect(document.querySelector<HTMLElement>("form")!.style.viewTransitionName).toBe("");
  await act(async () => root.render(<Target ready={false} snapshotReady />));
  await act(async () => transition.paint());
  expect(document.querySelector<HTMLElement>("form")!.style.viewTransitionName).toBe("project-composer");
  // Repeated readiness must not leave names behind or trigger another send.
  revealProjectTaskTransition(scope.threadId);
  await act(async () => transition.finish());
  expect(send).toHaveBeenCalledTimes(1);
  expect(document.querySelector<HTMLElement>("form")!.style.viewTransitionName).toBe("");
  await act(async () => root.unmount());
});
it("does not send if the destination is left before runtime readiness", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const transition = mockTransition();
  const scope = { userId: "transition-user", threadId: "left-task" };
  armProjectTaskLaunch(scope.userId, scope.threadId, "不要误发");
  startProjectTaskTransition(scope.threadId, () => {});
  const send = vi.fn(async () => true);
  function Target() { useProjectTaskLaunch(scope, false, send); return null; }
  const root = createRoot(document.createElement("div"));
  await act(async () => root.render(<Target />));
  await act(async () => root.unmount());
  await act(async () => transition.finish());
  expect(send).not.toHaveBeenCalled();
});
it("releases a stalled snapshot and falls back for reduced motion", async () => {
  vi.useFakeTimers(); const transition = mockTransition(); const navigate = vi.fn();
  startProjectTaskTransition("slow", navigate);
  await vi.advanceTimersByTimeAsync(1200);
  expect(transition.skip).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledOnce();
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  startProjectTaskTransition("reduced", navigate);
  expect(transition.start).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledTimes(2);
});

it("cancels an older handoff without leaving named snapshots or global styles behind", async () => {
  const transition = mockTransition();
  document.body.innerHTML = '<div data-project-launch-composer><form class="aui-composer-root"></form></div>';
  startProjectTaskTransition("old", () => {});
  revealProjectTaskTransition("old");
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: true })));
  const navigate = vi.fn();
  startProjectTaskTransition("next", navigate);
  await transition.paint();
  expect(transition.skip).toHaveBeenCalledOnce();
  expect(navigate).toHaveBeenCalledOnce();
  expect(document.querySelector<HTMLElement>("form")!.style.viewTransitionName).toBe("");
  expect(document.documentElement.hasAttribute("data-project-task-transition")).toBe(false);
});
