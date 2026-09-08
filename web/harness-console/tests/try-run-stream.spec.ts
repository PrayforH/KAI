import { describe, expect, it } from "vitest";
import type { StudioTryRun } from "../src/lib/studio-client";
import {
  appendTryRunEvent,
  projectTryRunConversation,
} from "../src/components/agent-studio/try-run-stream";

type RunEvent = StudioTryRun["events"][number];

function event(sequence: number, type: string, text = ""): RunEvent {
  return {
    event_id: `event-${sequence}`,
    sequence,
    type,
    timestamp: "2026-09-05T00:00:00Z",
    payload: text ? { text } : {},
  };
}

function view(): StudioTryRun {
  return {
    draftId: "draft-1",
    draftRevision: 1,
    run: {
      run_id: "run-1",
      session_id: "session-1",
      status: "running",
      error_code: null,
    },
    events: [],
    approvals: [],
    artifacts: [],
    finalText: "",
    loop: [],
  };
}

describe("Studio Try Run streaming projection", () => {
  it("keeps pre-tool progress in the process area and streams the latest answer", () => {
    const projected = projectTryRunConversation([
      event(1, "message.delta", "先检查资料。"),
      event(2, "tool.request"),
      event(3, "tool.result"),
      event(4, "message.delta", "这是"),
      event(5, "message.delta", "最终回答。"),
    ]);

    expect(projected).toEqual({
      processText: "先检查资料。",
      answerText: "这是最终回答。",
    });
  });

  it("appends deltas once and follows terminal run events", () => {
    const first = appendTryRunEvent(view(), event(1, "message.delta", "逐步"));
    const duplicate = appendTryRunEvent(first, event(1, "message.delta", "逐步"));
    const terminal = appendTryRunEvent(duplicate, event(2, "run.succeeded"));

    expect(duplicate.events).toHaveLength(1);
    expect(terminal.finalText).toBe("逐步");
    expect(terminal.run.status).toBe("succeeded");
  });
});
