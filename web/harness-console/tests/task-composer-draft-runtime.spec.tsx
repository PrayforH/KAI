// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { Attachment, CreateAttachment } from "@assistant-ui/react";
import { useTaskComposerSnapshot } from "../src/components/task-composer-draft";
import { pendingNewTaskDraft, rememberNewTaskDraft, loadTaskComposerSnapshot } from "../src/lib/task-composer-draft";

const runtime = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@assistant-ui/react", () => ({ useComposerRuntime: () => runtime.current }));
function composer() {
  let state: { text: string; attachments: Attachment[] } = { text: "", attachments: [] };
  const listeners = new Set<() => void>();
  const notify = () => listeners.forEach(listener => listener());
  return { getState: () => state, subscribe: (listener: () => void) => { listeners.add(listener); return () => listeners.delete(listener); },
    setText: (text: string) => { state = { ...state, text }; notify(); },
    addAttachment: vi.fn(async (value: File | CreateAttachment) => {
      const attachment: Attachment = value instanceof File ? { id: "upload:pending", name: value.name, type: "document", file: value, status: { type: "running", reason: "uploading", progress: 0 } }
        : { ...value, id: value.id!, type: value.type ?? "document", status: { type: "complete" } };
      state = { ...state, attachments: [...state.attachments, attachment] }; notify();
    }), reset: () => { state = { text: "", attachments: [] }; notify(); } };
}
function Draft({ threadId }: { threadId: string }) { useTaskComposerSnapshot({ userId: "draft-user", threadId }); return null; }
beforeEach(() => {
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
});
afterEach(() => { vi.unstubAllGlobals(); });

test("new task → history → new task restores the last keystroke and uploaded attachment", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const root = createRoot(document.createElement("div"));
  const draft = composer(); runtime.current = draft;
  await act(async () => root.render(<Draft threadId="new-draft" />));
  rememberNewTaskDraft(localStorage, "draft-user", "new-draft");
  await act(async () => {
    draft.setText("刚编辑的说明，无需等待防抖");
    await draft.addAttachment({ id: "input_artifact_image", type: "image", name: "参考.png", contentType: "image/png", content: [{ type: "file", data: "input_artifact_image", mimeType: "image/png" }] });
  });
  await act(async () => root.unmount());
  expect(pendingNewTaskDraft(localStorage, "draft-user")).toBe("new-draft");
  const history = composer(); runtime.current = history;
  const historyRoot = createRoot(document.createElement("div"));
  await act(async () => historyRoot.render(<Draft threadId="old-task" />));
  expect(history.getState().text).toBe("");
  expect(history.getState().attachments).toHaveLength(0);
  await act(async () => historyRoot.unmount());
  const returned = composer(); runtime.current = returned;
  const returnedRoot = createRoot(document.createElement("div"));
  await act(async () => returnedRoot.render(<Draft threadId="new-draft" />));
  expect(returned.getState().text).toBe("刚编辑的说明，无需等待防抖");
  expect(returned.getState().attachments[0].id).toBe("input_artifact_image");
  expect(returned.addAttachment).toHaveBeenCalledTimes(1);
  await act(async () => returned.reset());
  expect(pendingNewTaskDraft(localStorage, "draft-user")).toBeNull();
  await act(async () => returnedRoot.unmount());
});

test("attachment-only drafts remain reachable and uploading files survive a thread switch", async () => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  const root = createRoot(document.createElement("div"));
  const draft = composer(); runtime.current = draft;
  await act(async () => root.render(<Draft threadId="pending-file" />));
  rememberNewTaskDraft(localStorage, "draft-user", "pending-file");
  const file = new File(["payload"], "还在上传.txt", { type: "text/plain" });
  await act(async () => draft.addAttachment(file));
  await act(async () => root.unmount());
  expect(pendingNewTaskDraft(localStorage, "draft-user")).toBe("pending-file");
  expect(loadTaskComposerSnapshot(localStorage, "draft-user", "pending-file").attachments[0]).toBe(file);
  expect(loadTaskComposerSnapshot(localStorage, "other-user", "pending-file").attachments).toHaveLength(0);
});
