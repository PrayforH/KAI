// @vitest-environment jsdom
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {beforeEach,afterEach,it,expect,vi} from "vitest";
import {FeedbackToast} from "../src/components/feedback-toast";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement; let root: Root;
beforeEach(() => {vi.useFakeTimers();host=document.createElement("div");document.body.append(host);root=createRoot(host);});
afterEach(() => {act(() => root.unmount());host.remove();vi.useRealTimers();});
it("stacks notices outside page layout and expires information while retaining errors", async () => {
  const dismissed=vi.fn();
  await act(async () => root.render(<><FeedbackToast message="保存完成" onDismiss={dismissed}/><FeedbackToast message="连接失败" tone="error"/></>));
  expect(host.textContent).toBe("");
  expect(document.querySelectorAll('[aria-label="操作提示"]')).toHaveLength(1);
  expect(document.querySelector('[role="status"]')?.textContent).toContain("保存完成");
  await act(async () => vi.advanceTimersByTime(5000));
  expect(dismissed).toHaveBeenCalledTimes(1);
  expect(document.querySelector('[role="status"]')).toBeNull();
  expect(document.querySelector('[role="alert"]')?.textContent).toContain("连接失败");
  await act(async () => (document.querySelector('[aria-label="关闭提示"]') as HTMLButtonElement).click());
  expect(document.querySelector('[role="alert"]')).toBeNull();
});
it("cleans up timers on navigation and displays a new message after dismissal", async () => {
  const dismissed=vi.fn();
  await act(async () => root.render(<FeedbackToast message="下载已开始" onDismiss={dismissed}/>));
  await act(async () => (document.querySelector('[aria-label="关闭提示"]') as HTMLButtonElement).click());
  await act(async () => root.render(<FeedbackToast message="复制完成" onDismiss={dismissed}/>));
  expect(document.querySelector('[role="status"]')?.textContent).toContain("复制完成");
  await act(async () => root.render(null));
  expect(document.querySelector('[aria-label="操作提示"]')).toBeNull();
  await act(async () => vi.advanceTimersByTime(5000));
  expect(dismissed).toHaveBeenCalledTimes(1);
});
