// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import {
  ConversationScopeProvider,
  type ConversationScope,
} from "../src/lib/conversation-scope";
import {
  activityStore,
  useRunActivity,
  useRunViewModel,
} from "../src/lib/activity-store";
import { runActivitySchema } from "../src/lib/activity-schema";
import { approvalStore, usePendingApproval } from "../src/lib/approval-store";
import { runStreamStore, useRunStream } from "../src/lib/run-stream-store";
import { useLiveResponse } from "../src/lib/live-response-store";
import { useRunReuseNotice } from "../src/lib/run-reuse-store";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
it("isolates preview state while leaving the main conversation and its pending approval intact", () => {
  const host = document.createElement("div");
  const root = createRoot(host);
  const main = runActivitySchema.parse({
    run_id: "main-run",
    status: "running",
    started_at: "2026-09-21T00:00:00Z",
    items: [],
    metrics: {},
  });
  activityStore.publish(main);
  runStreamStore.startRun("main-run");
  approvalStore.show({ approval_id: "main-approval", run_id: "main-run" });
  const preview: ConversationScope = {
    stream: { status: "idle" },
    live: { text: "", status: "idle", visible: false },
    approval: { visible: false },
    onNew: () => {},
    onOpenFiles: () => {},
    onConfigureKnowledge: () => {},
    onApproval: async () => {},
    afterMessage: () => null,
  };
  function Probe({ name }: { name: string }) {
    const activity = useRunActivity();
    const view = useRunViewModel();
    const stream = useRunStream();
    const approval = usePendingApproval();
    const live = useLiveResponse();
    const reuse = useRunReuseNotice();
    return (
      <output data-name={name}>
        {JSON.stringify({
          activity: activity?.run_id,
          view: view?.runId,
          stream: stream.runId,
          approval: approval.details?.approval_id,
          live: live.text,
          reuse,
        })}
      </output>
    );
  }
  try {
    act(() =>
      root.render(
        <>
          <Probe name="main" />
          <ConversationScopeProvider value={preview}>
            <Probe name="preview" />
          </ConversationScopeProvider>
        </>,
      ),
    );
    expect(host.querySelector('[data-name="main"]')?.textContent).toContain(
      "main-approval",
    );
    expect(
      host.querySelector('[data-name="preview"]')?.textContent,
    ).not.toContain("main-");
    act(() => runStreamStore.startRun("main-next"));
    expect(host.querySelector('[data-name="main"]')?.textContent).toContain(
      "main-next",
    );
    expect(
      host.querySelector('[data-name="preview"]')?.textContent,
    ).not.toContain("main-next");
    expect(approvalStore.getSnapshot().details?.approval_id).toBe(
      "main-approval",
    );
  } finally {
    act(() => root.unmount());
    activityStore.clear();
    runStreamStore.clear();
    approvalStore.reset();
  }
});
