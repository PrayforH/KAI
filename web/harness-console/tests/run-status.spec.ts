import { describe, expect, it } from "vitest";

import {
  FAILED_RUN_STATUSES,
  RUN_STATUS_LABELS,
  TERMINAL_RUN_STATUSES,
  isFailedRunStatus,
  isTerminalRunStatus,
  runFailureMessage,
} from "../src/lib/run-status";

describe("run status partitions", () => {
  it("mirrors the backend's terminal states", () => {
    // harness.core.models.RunStatus.terminal_statuses()
    expect([...TERMINAL_RUN_STATUSES].sort()).toEqual(
      ["cancelled", "failed", "rejected", "succeeded", "timed_out"].sort(),
    );
    expect([...FAILED_RUN_STATUSES].sort()).toEqual(["failed", "rejected", "timed_out"].sort());
  });

  it("treats running states as neither terminal nor failed", () => {
    for (const status of ["queued", "provisioning", "running", "waiting_approval", "cancelling"]) {
      expect(isTerminalRunStatus(status)).toBe(false);
      expect(isFailedRunStatus(status)).toBe(false);
    }
    expect(isTerminalRunStatus(undefined)).toBe(false);
    expect(isTerminalRunStatus(null)).toBe(false);
  });

  it("classifies every terminal state", () => {
    expect(isTerminalRunStatus("succeeded")).toBe(true);
    expect(isFailedRunStatus("succeeded")).toBe(false);
    expect(isTerminalRunStatus("cancelled")).toBe(true);
    expect(isFailedRunStatus("cancelled")).toBe(false);
    for (const status of FAILED_RUN_STATUSES) {
      expect(isTerminalRunStatus(status)).toBe(true);
      expect(isFailedRunStatus(status)).toBe(true);
    }
  });

  it("has a label for every state it can render", () => {
    expect(Object.keys(RUN_STATUS_LABELS).sort()).toEqual(
      [...TERMINAL_RUN_STATUSES, "queued", "provisioning", "running", "waiting_approval", "cancelling", "cancelled", "succeeded", "failed", "timed_out", "rejected"]
        .filter((value, index, all) => all.indexOf(value) === index)
        .sort(),
    );
  });
});

describe("failure messages", () => {
  it("translates the codes the platform actually emits", () => {
    expect(runFailureMessage("runtime_timeout")).toContain("超时");
    expect(runFailureMessage("runtime_error")).toContain("运行环境");
  });

  it("never shows an internal code to the reader", () => {
    for (const code of ["some_new_code", "", null, undefined]) {
      const message = runFailureMessage(code);
      expect(message).not.toBe(code ?? "");
      expect(message.length).toBeGreaterThan(0);
    }
  });
});
