import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const managerSource = readFileSync(
  join(process.cwd(), "src/components/agent-studio/assistant-manager.tsx"),
  "utf8",
);
const meteringSource = readFileSync(
  join(process.cwd(), "src/components/checkpoint-metering.tsx"),
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
    // 文案口径已抽到共享组件，托管面板与进展浮标共用同一份。
    expect(meteringSource).toContain('toLocaleString("zh-CN")');
    expect(meteringSource).toContain("`tokens ${tokens!");
    expect(meteringSource).toContain("cost $${metrics.costUsd}");
    expect(meteringSource).toContain("耗时 ${Math.max(1, Math.round(durationSeconds))} 秒");
    expect(managerSource).toContain("<CheckpointMeteringLine");
  });

  it("shows 未计量 instead of a fake 0 when usage is unknown", () => {
    // unknownUsage 或 tokens 缺失 → 明确「tokens 未计量」，不得渲染 tokens 0。
    expect(meteringSource).toContain('"tokens 未计量"');
    expect(meteringSource).toContain("unknownUsage || tokens == null");
    expect(meteringSource).not.toContain("tokens 0");
    expect(managerSource).not.toContain("tokens 0");
  });

  it("backend projects checkpoint metrics by run_id and duration from the run", () => {
    expect(apiSource).toContain("metrics_by_run[checkpoint.run_id] = {");
    expect(apiSource).toContain('"unknownUsage": True');
    expect(serviceSource).toContain('"duration_seconds": duration_seconds');
    expect(serviceSource).toContain('"run_id": latest.run_id');
  });
});
