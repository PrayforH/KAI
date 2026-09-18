// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TaskHeaderActions } from "../src/components/task-header-actions";
import type { TaskSummary } from "../src/lib/task-history";

const { setTaskPinned, setTaskTitle, setTaskArchived, notifyTaskListChanged } = vi.hoisted(() => ({
  setTaskPinned: vi.fn(),
  setTaskTitle: vi.fn(),
  setTaskArchived: vi.fn(),
  notifyTaskListChanged: vi.fn(),
}));

vi.mock("../src/lib/task-history", () => ({
  setTaskPinned,
  setTaskTitle,
  setTaskArchived,
  notifyTaskListChanged,
  peekCachedTasks: vi.fn(() => null),
}));

vi.mock("../src/lib/task-list-age", () => ({ formatTaskAge: vi.fn(() => "7 小时") }));
vi.mock("../src/components/confirmation-dialog", () => ({
  useConfirmationDialog: () => ({
    requestConfirmation: vi.fn(async () => true),
    requestDecision: vi.fn(),
    confirmationDialog: null,
  }),
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function task(overrides: Partial<TaskSummary> = {}): TaskSummary {
  return {
    thread_id: "thread-1",
    run_id: "run-1",
    session_id: "session-1",
    title: "分析任务",
    agent_name: "agent-studio",
    agent_version: "0.2.0",
    agent_owner_user_id: "user-1",
    status: "succeeded",
    created_at: "2026-09-18T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
    ...overrides,
  };
}

describe("task header actions", () => {
  let container: HTMLDivElement;
  let root: Root | null = null;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    vi.clearAllMocks();
    setTaskPinned.mockResolvedValue(undefined);
    setTaskTitle.mockResolvedValue("新标题");
    setTaskArchived.mockResolvedValue(undefined);
  });

  afterEach(() => {
    act(() => root?.unmount());
    container.remove();
    root = null;
  });

  function render(component: React.ReactNode) {
    root = createRoot(container);
    act(() => {
      root!.render(component);
    });
  }

  function menuItems() {
    return Array.from(container.querySelectorAll(".task-header-menu button"))
      .map((button) => button.textContent?.trim());
  }

  it("exposes pin, rename and archive actions for the active task", () => {
    render(<TaskHeaderActions task={task()} />);
    act(() => {
      container.querySelector<HTMLButtonElement>(".task-header-more")?.click();
    });
    expect(menuItems()).toEqual([
      "置顶任务",
      "重命名任务",
      "归档任务",
    ]);
  });

  it("offers unpin for a pinned task and pins through the thread PATCH", async () => {
    render(<TaskHeaderActions task={task({ pinned_at: "2026-09-18T01:00:00Z" })} />);
    act(() => {
      container.querySelector<HTMLButtonElement>(".task-header-more")?.click();
    });
    expect(menuItems()).toEqual([
      "取消置顶任务",
      "重命名任务",
      "归档任务",
    ]);
    const pinButton = container
      .querySelectorAll<HTMLButtonElement>(".task-header-menu button")[0];
    await act(async () => {
      pinButton.click();
    });
    expect(setTaskPinned).toHaveBeenCalledWith("thread-1", false);
    expect(notifyTaskListChanged).toHaveBeenCalled();
  });

  it("renames through the thread PATCH and reports the new title", async () => {
    const onRenamed = vi.fn();
    render(<TaskHeaderActions task={task()} onRenamed={onRenamed} />);
    act(() => {
      container.querySelector<HTMLButtonElement>(".task-header-more")?.click();
    });
    act(() => {
      container
        .querySelectorAll<HTMLButtonElement>(".task-header-menu button")[1]
        ?.click();
    });
    const input = document.body.querySelector<HTMLInputElement>(".task-rename-dialog input");
    expect(input).not.toBeNull();
    act(() => {
      if (input) {
        // React tracks controlled inputs through the native value setter.
        const setValue = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        )?.set;
        setValue?.call(input, "新标题");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    const form = document.body.querySelector<HTMLFormElement>(".task-rename-dialog");
    await act(async () => {
      form?.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    });
    expect(setTaskTitle).toHaveBeenCalledWith("thread-1", "新标题");
    expect(onRenamed).toHaveBeenCalledWith("新标题");
    expect(notifyTaskListChanged).toHaveBeenCalled();
  });

  it("keeps archive disabled while the task is still running", () => {
    render(<TaskHeaderActions task={task({ status: "running" })} />);
    act(() => {
      container.querySelector<HTMLButtonElement>(".task-header-more")?.click();
    });
    const archiveButton = container
      .querySelectorAll<HTMLButtonElement>(".task-header-menu button")[2];
    expect(archiveButton.disabled).toBe(true);
    expect(setTaskArchived).not.toHaveBeenCalled();
  });

  it("archives finished tasks and notifies the task sidebars", async () => {
    const onArchived = vi.fn();
    render(<TaskHeaderActions task={task()} onArchived={onArchived} />);
    act(() => {
      container.querySelector<HTMLButtonElement>(".task-header-more")?.click();
    });
    await act(async () => {
      container
        .querySelectorAll<HTMLButtonElement>(".task-header-menu button")[2]
        ?.click();
    });
    expect(setTaskArchived).toHaveBeenCalledWith("thread-1", true);
    expect(onArchived).toHaveBeenCalled();
    expect(notifyTaskListChanged).toHaveBeenCalled();
  });
});
