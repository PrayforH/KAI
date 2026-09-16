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

it("shows and settles approval cards directly from streamed events", () => {
  const requested = { ...event(1, "approval.requested"), payload: {
    approval_id: "approval-1", tool_name: "Bash", reason: "确认操作", argument_summary: { command: "echo test" },
  } };
  const waiting = appendTryRunEvent(view(), requested);
  expect(waiting.run.status).toBe("waiting_approval");
  expect(waiting.approvals[0]).toMatchObject({ approval_id: "approval-1", status: "pending", tool_name: "Bash" });
  for (const status of ["approved", "rejected", "expired", "cancelled"]) {
    const decided = appendTryRunEvent(waiting, { ...event(2, `approval.${status}`), payload: { approval_id: "approval-1" } });
    expect(decided.approvals[0].status).toBe(status);
  }
  const resumed = appendTryRunEvent(waiting, event(3, "run.running"));
  expect(resumed.run.status).toBe("running");
  expect(appendTryRunEvent(resumed, event(2, "run.waiting_approval")).run.status).toBe("running");
});
