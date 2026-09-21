import { describe, expect, it } from "vitest";
import { resolveTaskLaunchMode } from "../src/lib/task-launch";

describe("task launch mode", () => {
  it("creates a new task even before the previous history has loaded", () => {
    expect(resolveTaskLaunchMode("empty", "new-task")).toBe("create-thread");
    expect(resolveTaskLaunchMode("unknown", "new-task")).toBe("create-thread");
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
