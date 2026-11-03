// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { useMentionHistoryUpdates } from "../src/components/assistant-mention-bar";
import { handoverStore } from "../src/lib/handover-store";
vi.mock("../src/lib/mention-completion", () => ({ watchMentionCompletion: async () => true }));
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: async () => [] }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let cleanup: (() => Promise<void>) | undefined;
afterEach(async () => { await cleanup?.(); cleanup = undefined; });
it("reconciles the origin timeline on send/completion without rendering a bottom receipt list", async () => {
  handoverStore.ingestMention({ handoverId: "receipt-timeline", specialistId: "weather", specialistName: "天气提醒", sourceThreadId: "main", runId: "child" });
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host); const changed = vi.fn();
  function Observer({ thread }: {thread: string}) { useMentionHistoryUpdates(thread, changed); return null; }
  cleanup = async () => { await act(async () => root.unmount()); host.remove(); };
  await act(async () => root.render(<Observer thread="rest" />));
  expect(changed).not.toHaveBeenCalled();
  await act(async () => root.render(<Observer thread="main" />));
  expect(changed).toHaveBeenCalledTimes(2); // Actual delivery, then actual completion.
  expect(host.innerHTML).toBe("");
});
