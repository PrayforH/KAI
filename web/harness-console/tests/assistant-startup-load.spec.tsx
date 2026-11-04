// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { useMentionHistoryUpdates } from "../src/components/assistant-mention-bar";
import { AssistantComputerLink } from "../src/components/assistant-computer-link";
import { AssistantComputerPane } from "../src/components/assistant-computer-pane";

const { watch, desktop } = vi.hoisted(() => ({ watch: vi.fn(async () => true), desktop: vi.fn(() => null) }));
vi.mock("../src/lib/mention-completion", () => ({ watchMentionCompletion: watch }));
vi.mock("../src/components/computer-desktop", () => ({ ComputerDesktop: desktop }));
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: async (path: string) => path === "handovers"
  ? Array.from({length: 23}, (_, i) => ({ handoverId: `old-${i}`, messageId: `mention-specialist:old-${i}`,
      specialistId: "child", sourceThreadId: "main", targetThreadId: "child-thread", runId: `old-run-${i}`,
      status: "sent", createdAt: "2026-11-03T00:00:00Z" })) : [] }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let cleanup: (() => Promise<void>) | undefined;
afterEach(async () => { await cleanup?.(); cleanup = undefined; vi.unstubAllGlobals(); });

it("opening old main chat does not replay 23 child Runs or force another history import", async () => {
  const host = document.createElement("div"); const root = createRoot(host); const changed = vi.fn();
  function Observer() { useMentionHistoryUpdates("main", changed); return null; }
  cleanup = async () => { await act(async () => root.unmount()); };
  await act(async () => root.render(<Observer />));
  expect(watch).not.toHaveBeenCalled();
  expect(changed).not.toHaveBeenCalled();
});

it("closed computer makes no status request and does not mount its desktop", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  const host = document.createElement("div"); const root = createRoot(host);
  cleanup = async () => { await act(async () => root.unmount()); };
  await act(async () => root.render(<AssistantComputerPane open={false} onClose={() => {}}>
    <AssistantComputerLink open={false} onToggle={() => {}} />
  </AssistantComputerPane>));
  expect(fetcher).not.toHaveBeenCalled(); expect(desktop).not.toHaveBeenCalled();
  expect(host.querySelector('[aria-label="打开电脑"]')).not.toBeNull();
  await act(async () => root.render(<AssistantComputerPane open onClose={() => {}}>chat</AssistantComputerPane>));
  expect(desktop).toHaveBeenCalled();
});
