import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const managerSource = readFileSync(
  join(process.cwd(), "src/components/agent-studio/assistant-manager.tsx"),
  "utf8",
);
const apiSource = readFileSync(
  join(process.cwd(), "../../src/harness/assistant/api.py"),
  "utf8",
);
const serviceSource = readFileSync(
  join(process.cwd(), "../../src/harness/assistant/service.py"),
  "utf8",
);

describe("managed panel metering", () => {
  it("shows real tokens/cost and duration when a checkpoint measured them", () => {
    expect(managerSource).toContain("tokens ${task.progress.metrics.tokens.toLocaleString");
    expect(managerSource).toContain("cost $${task.progress.metrics.costUsd}");
    expect(managerSource).toContain("耗时 ${Math.max(1, Math.round(task.progress.duration_seconds))} 秒");
  });

  it("shows 未计量 instead of a fake 0 when usage is unknown", () => {
    // unknownUsage 或 tokens 缺失 → 明确「tokens 未计量」，不得渲染 tokens 0。
    expect(managerSource).toContain('"tokens 未计量"');
    expect(managerSource).toContain("unknownUsage || task.progress.metrics.tokens == null");
    expect(managerSource).not.toContain("tokens 0");
  });

  it("backend projects checkpoint metrics by run_id and duration from the run", () => {
    expect(apiSource).toContain("metrics_by_run[checkpoint.run_id] = {");
    expect(apiSource).toContain('"unknownUsage": True');
    expect(serviceSource).toContain('"duration_seconds": duration_seconds');
    expect(serviceSource).toContain('"run_id": latest.run_id');
  });
});
