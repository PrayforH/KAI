import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  TaskModelControl,
  TaskModelProvider,
} from "../src/components/task-model-context";
import type { TaskModelRoute } from "../src/lib/task-model-catalog";

const routes: TaskModelRoute[] = [
  {
    id: "deepseek-v4-flash",
    label: "DeepSeek V4 Flash",
    provider: "deepseek",
    model: "deepseek-v4-flash",
    modelType: "chat" as const,
    capabilities: ["streaming", "tool_use"],
  },
  {
    id: "deepseek-v4-pro",
    label: "DeepSeek V4 Pro",
    provider: "deepseek",
    model: "deepseek-v4-pro",
    modelType: "chat" as const,
    capabilities: ["streaming", "tool_use"],
  },
];

describe("TaskModelControl", () => {
  it("shows the Agent default model only once", () => {
    const html = renderToStaticMarkup(
      <TaskModelProvider
        routes={routes}
        agentDefaultRouteId="deepseek-v4-flash"
        overrideRouteId={null}
        onOverrideChange={() => undefined}
      >
        <TaskModelControl disabled={false} />
      </TaskModelProvider>,
    );

    expect(html.match(/DeepSeek V4 Flash/g)).toHaveLength(1);
    expect(html).toContain("DeepSeek V4 Pro");
  });

  it("normalizes a stored override that matches the Agent default", () => {
    const html = renderToStaticMarkup(
      <TaskModelProvider
        routes={routes}
        agentDefaultRouteId="deepseek-v4-flash"
        overrideRouteId="deepseek-v4-flash"
        onOverrideChange={() => undefined}
      >
        <TaskModelControl disabled={false} />
      </TaskModelProvider>,
    );

    expect(html).toContain('<option value="" selected="">DeepSeek V4 Flash</option>');
    expect(html).toContain("跟随 Agent");
    expect(html).not.toContain("仅本次任务");
  });
});
