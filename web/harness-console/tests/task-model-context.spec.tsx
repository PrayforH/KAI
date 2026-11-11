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
  it("shows 自动 without naming the Agent default model", () => {
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

    expect(html).toContain('<option value="" selected="">自动</option>');
    expect(html).not.toContain("DeepSeek V4 Flash");
    expect(html).toContain("DeepSeek V4 Pro");
  });

  it("keeps the 自动 label when the Agent default route is unavailable", () => {
    const html = renderToStaticMarkup(
      <TaskModelProvider
        routes={routes}
        agentDefaultRouteId={null}
        overrideRouteId={null}
        onOverrideChange={() => undefined}
      >
        <TaskModelControl disabled={false} />
      </TaskModelProvider>,
    );

    expect(html).toContain('<option value="" selected="">自动</option>');
    expect(html).not.toContain("自动 · ");
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

    expect(html).toContain('<option value="" selected="">自动</option>');
    // The label is just 自动; the rules live in the title.
    expect(html).toContain(">自动<");
    expect(html).toContain("需要读图时切到支持视觉的路由");
    expect(html).not.toContain("仅本次任务");
  });
  it("keeps independent builder choices explicit without an Agent default option", () => {
    const html = renderToStaticMarkup(<TaskModelProvider independent routes={routes}
      agentDefaultRouteId="deepseek-v4-flash" overrideRouteId="deepseek-v4-flash"
      onOverrideChange={() => undefined}><TaskModelControl disabled={false} /></TaskModelProvider>);
    expect(html).toContain('<option value="deepseek-v4-flash" selected="">');
    expect(html).not.toContain('value=""');
    expect(html).not.toContain("自动");
    expect(html).toContain("当前对话");
  });

});
