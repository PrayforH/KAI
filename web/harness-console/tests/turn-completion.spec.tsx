// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const mock = vi.hoisted(() => ({ fork: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mock.push }) }));
vi.mock("../src/lib/task-history", () => ({ forkTask: mock.fork }));
vi.mock("../src/components/feedback-toast", () => ({ FeedbackToast: ({ message }: { message: string }) => message ? <span role="alert">{message}</span> : null }));
import { TurnCompletionControl } from "../src/components/turn-completion";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const time = "2026-10-20T16:30:00Z";
const scope = { threadId: "source-thread", userId: "owner" };
beforeEach(() => { vi.clearAllMocks(); host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });
it("keeps the completion time without exposing a branch on historical or scoped turns", async () => {
  await act(async () => root.render(<TurnCompletionControl completedAt={time} />));
  expect(host.querySelector("time")?.dateTime).toBe(time);
  expect(host.querySelector("button")).toBeNull();
  expect(mock.fork).not.toHaveBeenCalled();
});
it("creates one independent task and navigates only after the branch is saved", async () => {
  let resolve!: (task: { thread_id: string }) => void;
  mock.fork.mockReturnValue(new Promise(result => { resolve = result; }));
  await act(async () => root.render(<TurnCompletionControl completedAt={time} branchScope={scope} />));
  const button = host.querySelector("button")!;
  expect(button.getAttribute("aria-label")).toBe("分支到新任务");
  expect(host.querySelector("time")?.dateTime).toBe(time);
  await act(async () => { button.click(); button.click(); });
  expect(mock.fork).toHaveBeenCalledExactlyOnceWith(scope.threadId, scope.userId);
  expect(button.disabled).toBe(true);
  expect(mock.push).not.toHaveBeenCalled();
  await act(async () => resolve({ thread_id: "new-branch" }));
  expect(mock.push).toHaveBeenCalledExactlyOnceWith("/?thread=new-branch");
});
it("keeps the original conversation available when branching fails and permits retry", async () => {
  mock.fork.mockRejectedValueOnce(new Error("当前任务仍在执行"));
  await act(async () => root.render(<TurnCompletionControl completedAt={time} branchScope={scope} />));
  await act(async () => host.querySelector("button")!.click());
  expect(host.querySelector('[role="alert"]')?.textContent).toBe("当前任务仍在执行");
  expect(host.querySelector("time")?.dateTime).toBe(time);
  expect(mock.push).not.toHaveBeenCalled();
  expect(host.querySelector("button")?.disabled).toBe(false);
  mock.fork.mockResolvedValueOnce({ thread_id: "retry-branch" });
  await act(async () => host.querySelector("button")!.click());
  expect(mock.push).toHaveBeenCalledWith("/?thread=retry-branch");
});
it("does not navigate back after the user leaves while a fork is pending", async () => {
  let resolve!: (task: { thread_id: string }) => void;
  mock.fork.mockReturnValue(new Promise(result => { resolve = result; }));
  await act(async () => root.render(<TurnCompletionControl completedAt={time} branchScope={scope} />));
  await act(async () => host.querySelector("button")!.click());
  await act(async () => root.render(null));
  await act(async () => resolve({ thread_id: "late-branch" }));
  expect(mock.push).not.toHaveBeenCalled();
});

it("hands the saved task to the mounted conversation's task switcher", async () => {
  const onForked = vi.fn();
  const branch = { thread_id: "selected-branch" };
  mock.fork.mockResolvedValueOnce(branch);
  await act(async () => root.render(<TurnCompletionControl completedAt={time} branchScope={scope} onForked={onForked} />));
  await act(async () => host.querySelector("button")!.click());
  expect(onForked).toHaveBeenCalledExactlyOnceWith(branch);
  expect(mock.push).not.toHaveBeenCalled();
});
