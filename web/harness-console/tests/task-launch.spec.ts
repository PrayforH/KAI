import { describe, expect, it } from "vitest";
import { resolveTaskLaunchMode } from "../src/lib/task-launch";

describe("task launch mode", () => {
  it("focuses the current shell when it has no durable task yet", () => {
    expect(resolveTaskLaunchMode("empty", "new-task")).toBe("focus-current");
    expect(resolveTaskLaunchMode("unknown", "new-task")).toBe("focus-current");
  });

  it("reuses an empty shell when the user selects an Agent", () => {
    expect(resolveTaskLaunchMode("empty", "select-agent")).toBe("reuse-current");
  });

  it("keeps durable and unknown Agent bindings isolated", () => {
    expect(resolveTaskLaunchMode("durable", "new-task")).toBe("create-thread");
    expect(resolveTaskLaunchMode("durable", "select-agent")).toBe("create-thread");
    expect(resolveTaskLaunchMode("unknown", "select-agent")).toBe("create-thread");
  });
});
