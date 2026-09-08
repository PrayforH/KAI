import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ActivitySummary,
  activeElapsedMs,
  formatResultPreview,
} from "../src/components/activity-summary";
import type { RunViewModel } from "../src/lib/run-view-model";
import { runActivitySchema } from "../src/lib/activity-schema";

const activity = runActivitySchema.parse({
  run_id: "run-ribbon",
  status: "running",
  started_at: "2026-07-14T00:00:00Z",
  metrics: {},
  items: [
    {
      id: "run-start",
      event_type: "run.running",
      kind: "run",
      status: "running",
      title: "Agent 开始执行",
      timestamp: "2026-07-14T00:00:01Z",
      sequence: 1,
      metadata: {},
    },
    {
      id: "task-a-start",
      event_type: "subagent.started",
      kind: "subagent",
      status: "running",
      title: "子 Agent 正在执行",
      summary: "分析依赖关系",
      timestamp: "2026-07-14T00:00:02Z",
      sequence: 2,
      metadata: {
        task_id: "task-a",
        parent_tool_use_id: "root",
        alias: "fact-checker",
        agent_version: "1.0.0",
      },
    },
    {
      id: "task-a-complete",
      event_type: "subagent.completed",
      kind: "subagent",
      status: "succeeded",
      title: "子 Agent 已完成",
      summary: "分析依赖关系",
      timestamp: "2026-07-14T00:00:03Z",
      sequence: 3,
      metadata: {
        task_id: "task-a",
        parent_tool_use_id: "root",
        alias: "fact-checker",
        agent_version: "1.0.0",
        duration_ms: 1000,
        usage: { tool_uses: 2 },
      },
    },
    {
      id: "task-b-start",
      event_type: "subagent.started",
      kind: "subagent",
      status: "running",
      title: "子 Agent 正在执行",
      summary: "检查审批边界",
      timestamp: "2026-07-14T00:00:04Z",
      sequence: 4,
      metadata: {
        task_id: "task-b",
        parent_tool_use_id: "root",
        alias: "risk-reviewer",
        agent_version: "1.0.0",
      },
    },
    {
      id: "tool-one",
      event_type: "tool.request",
      kind: "tool",
      status: "running",
      title: "调用 Read",
      timestamp: "2026-07-14T00:00:05Z",
      sequence: 5,
      metadata: {
        tool_call_id: "tool-1",
        name: "Read",
        arguments: { file_path: "docs/agent-production-platform-design.md" },
      },
    },
    {
      id: "tool-two",
      event_type: "tool.request",
      kind: "tool",
      status: "running",
      title: "调用 Grep",
      timestamp: "2026-07-14T00:00:06Z",
      sequence: 6,
      metadata: {
        tool_call_id: "tool-2",
        name: "Grep",
        arguments: {
          pattern: "publishDraft|promote",
          path: "web/harness-console",
        },
      },
    },
  ],
});

it("uses a browser-local elapsed anchor instead of subtracting server wall time", () => {
  const view = {
    runId: "run-clock-skew",
    phase: "running",
    startedAt: "2026-07-14T00:00:00Z",
    updatedAt: "2026-07-14T00:00:00.500Z",
    elapsedMs: 500,
    summary: "准备执行",
    items: [],
    tasks: [],
    tools: [],
    taskCount: 0,
    toolCount: 0,
  } satisfies RunViewModel;

  expect(
    activeElapsedMs(view, 2_000, {
      runId: view.runId,
      observedAt: 1_000,
      elapsedMs: 500,
    }),
  ).toBe(1_500);
  const started = Date.parse(view.startedAt);
  expect(activeElapsedMs(view, started + 30_000, {
    runId: view.runId, observedAt: started + 29_000, elapsedMs: 500,
  })).toBe(1_500);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("execution ribbon", () => {
  it("pretty prints JSON results while preserving non-JSON command output", () => {
    expect(formatResultPreview('{"items":[{"name":"demo","ok":true}]}')).toBe(
      '{\n  "items": [\n    {\n      "name": "demo",\n      "ok": true\n    }\n  ]\n}',
    );
    expect(formatResultPreview("line one\nline two")).toBe(
      "line one\nline two",
    );
    expect(formatResultPreview("{not-json}")).toBe("{not-json}");
  });

  it("expands the observable process by default while work is active", () => {
    const html = renderToStaticMarkup(<ActivitySummary activity={activity} />);

    expect(html).toContain(
      '<section class="execution-ribbon phase-running" aria-label="执行进度 run-ribbon" data-run-id="run-ribbon" data-response-started="false" data-open="true">',
    );
    expect(html).toContain("正在处理");
    expect(html).toContain("已持续 6s");
    expect(html).toContain('aria-expanded="true"');
    expect(html).not.toContain('class="execution-state-mark"');
    expect(html).not.toContain('class="execution-summary-icon"');
    expect(html).toContain("运行了 2 个子任务");
    expect(html).toContain("搜索了 1 次内容 · 读取了 1 个文件");
    expect(html).toContain("6s");
  });

  it("keeps active work expanded when response text starts", () => {
    const html = renderToStaticMarkup(
      <ActivitySummary activity={activity} responseStarted />,
    );

    expect(html).toContain(
      '<section class="execution-ribbon phase-running" aria-label="执行进度 run-ribbon" data-run-id="run-ribbon" data-response-started="true" data-open="true">',
    );
    expect(html).toContain('aria-expanded="true"');
  });

  it("restores a manually collapsed failed run by run id", () => {
    const failed = runActivitySchema.parse({
      ...activity,
      run_id: "run-persisted-failure",
      status: "failed",
      items: [
        ...activity.items,
        {
          id: "run-failed-persisted",
          event_type: "run.failed",
          kind: "run",
          status: "failed",
          title: "运行失败",
          summary: "测试失败",
          timestamp: "2026-07-14T00:00:08Z",
          sequence: 8,
          metadata: {},
        },
      ],
    });
    vi.stubGlobal("window", {
      localStorage: {
        getItem(key: string) {
          return key.endsWith("run-persisted-failure") ? "closed" : null;
        },
        setItem() {},
      },
    });

    const html = renderToStaticMarkup(<ActivitySummary activity={failed} />);

    expect(html).toContain('data-open="false"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('<div class="execution-tree" hidden="">');
  });

  it("automatically folds the completed Codex transcript", () => {
    const completed = runActivitySchema.parse({
      ...activity,
      status: "succeeded",
      items: [
        ...activity.items,
        {
          id: "run-succeeded",
          event_type: "run.succeeded",
          kind: "run",
          status: "completed",
          title: "运行完成",
          summary: "运行已完成",
          timestamp: "2026-07-14T00:00:08Z",
          sequence: 8,
          metadata: {},
        },
      ],
    });
    const waitingForResponse = renderToStaticMarkup(
      <ActivitySummary activity={completed} />,
    );
    const responseMounted = renderToStaticMarkup(
      <ActivitySummary activity={completed} responseStarted />,
    );

    expect(waitingForResponse).toContain('data-response-started="false"');
    expect(waitingForResponse).toContain('data-open="false"');
    expect(responseMounted).toContain('data-response-started="true"');
    expect(responseMounted).toContain('data-open="false"');
  });

  it("streams trailing model text in processing and removes it after completion", () => {
    const narrated = runActivitySchema.parse({
      ...activity,
      items: [
        activity.items[0],
        {
          id: "commentary-before-read",
          event_type: "message.delta",
          kind: "analysis",
          status: "succeeded",
          title: "进展说明",
          summary: "我先读取 **设计文档**，再检查发布边界。",
          timestamp: "2026-07-14T00:00:02Z",
          sequence: 2,
          metadata: { message_id: "assistant-progress" },
        },
        ...activity.items.slice(1).map((item) => ({
          ...item,
          sequence: item.sequence + 1,
        })),
        {
          id: "final-answer",
          event_type: "message.delta",
          kind: "analysis",
          status: "succeeded",
          title: "进展说明",
          summary: "最终结论已经整理完成。",
          timestamp: "2026-07-14T00:00:08Z",
          sequence: 8,
          metadata: { message_id: "assistant-final" },
        },
      ],
    });
    const html = renderToStaticMarkup(<ActivitySummary activity={narrated} />);
    const completedHtml = renderToStaticMarkup(
      <ActivitySummary
        activity={runActivitySchema.parse({
          ...narrated,
          status: "succeeded",
          items: [
            ...narrated.items,
            {
              id: "run-succeeded-after-answer",
              event_type: "run.succeeded",
              kind: "run",
              status: "succeeded",
              title: "运行完成",
              timestamp: "2026-07-14T00:00:09Z",
              sequence: 9,
              metadata: {},
            },
          ],
        })}
      />,
    );

    expect(html).toContain("我先读取 <strong>设计文档</strong>，再检查发布边界。");
    expect(html).not.toContain('<strong>进展</strong>');
    expect(html).toContain('data-active="false"');
    expect(html).not.toContain("最终结论已经整理完成。");
    expect(completedHtml).not.toContain("最终结论已经整理完成。");
    expect(html.indexOf("我先读取")).toBeLessThan(
      html.indexOf("搜索了 1 次内容 · 读取了 1 个文件"),
    );
  });

  it("shows auditable processing milestones even when no tools are used", () => {
    const textOnly = runActivitySchema.parse({
      run_id: "run-text-only",
      status: "succeeded",
      started_at: "2026-07-14T00:00:00Z",
      metrics: { turns: 1 },
      items: [
        {
          id: "provisioning",
          event_type: "run.provisioning",
          kind: "run",
          status: "running",
          title: "正在准备运行环境",
          timestamp: "2026-07-14T00:00:01Z",
          sequence: 1,
          metadata: {},
        },
        {
          id: "runtime",
          event_type: "runtime.system",
          kind: "analysis",
          status: "running",
          title: "运行时与工具已连接",
          summary: "5 项工具可用",
          timestamp: "2026-07-14T00:00:02Z",
          sequence: 2,
          metadata: { subtype: "init", tool_count: 5 },
        },
        {
          id: "requesting",
          event_type: "runtime.system",
          kind: "analysis",
          status: "running",
          title: "模型正在处理",
          summary: "正在等待本轮模型结果",
          timestamp: "2026-07-14T00:00:03Z",
          sequence: 3,
          metadata: { subtype: "status" },
        },
        {
          id: "message-start",
          event_type: "message.start",
          kind: "analysis",
          status: "running",
          title: "正在生成本轮回复",
          timestamp: "2026-07-14T00:00:04Z",
          sequence: 4,
          metadata: {},
        },
        {
          id: "final-answer",
          event_type: "message.delta",
          kind: "analysis",
          status: "succeeded",
          title: "进展说明",
          summary: "这是单独渲染的最终回答。",
          timestamp: "2026-07-14T00:00:05Z",
          sequence: 5,
          metadata: {},
        },
        {
          id: "result",
          event_type: "runtime.result",
          kind: "result",
          status: "succeeded",
          title: "模型执行完成",
          timestamp: "2026-07-14T00:00:06Z",
          sequence: 6,
          metadata: { turns: 1 },
        },
        {
          id: "succeeded",
          event_type: "run.succeeded",
          kind: "run",
          status: "succeeded",
          title: "运行完成",
          timestamp: "2026-07-14T00:00:07Z",
          sequence: 7,
          metadata: {},
        },
      ],
    });
    const html = renderToStaticMarkup(<ActivitySummary activity={textOnly} />);

    expect(html).not.toContain("已准备运行环境");
    expect(html).not.toContain("已生成回复");
    expect(html).toContain("处理完成");
    expect(html).not.toContain("正在生成本轮回复");
    expect(html).not.toContain("模型执行完成");
    expect(html).not.toContain("这是单独渲染的最终回答。");
  });

  it("renders provider reasoning summaries as real expandable thought content", () => {
    const withReasoningSummary = runActivitySchema.parse({
      ...activity,
      items: [
        ...activity.items,
        {
          id: "reasoning-summary",
          event_type: "reasoning.summary.delta",
          kind: "analysis",
          status: "succeeded",
          title: "思考摘要",
          summary: "先核对配置，再运行测试。",
          timestamp: "2026-07-14T00:00:06.500Z",
          sequence: 7,
          metadata: { item_id: "reasoning-1" },
        },
      ],
    });

    const html = renderToStaticMarkup(
      <ActivitySummary activity={withReasoningSummary} />,
    );

    expect(html).toContain('data-commentary-source="reasoning_summary"');
    expect(html).toContain('data-active="true"');
    expect(html).toContain("先核对配置，再运行测试。");
    expect(html).not.toContain("模型摘要");
  });

  it("stops the current thought icon when answer output starts", () => {
    const withReasoningSummary = runActivitySchema.parse({
      ...activity,
      items: [
        ...activity.items,
        {
          id: "reasoning-before-response",
          event_type: "reasoning.summary.delta",
          kind: "analysis",
          status: "succeeded",
          title: "思考摘要",
          summary: "已经完成方案判断，准备输出。",
          timestamp: "2026-07-14T00:00:06.500Z",
          sequence: 7,
          metadata: { item_id: "reasoning-before-response" },
        },
      ],
    });

    const html = renderToStaticMarkup(
      <ActivitySummary activity={withReasoningSummary} responseStarted />,
    );

    expect(html).toContain('data-commentary-source="reasoning_summary"');
    expect(html).toContain('data-active="false"');
  });

  it("stops the thought icon when the model is no longer running", () => {
    const completed = runActivitySchema.parse({
      ...activity,
      status: "succeeded",
      items: [
        ...activity.items,
        {
          id: "reasoning-before-approval",
          event_type: "reasoning.summary.delta",
          kind: "analysis",
          status: "succeeded",
          title: "思考摘要",
          summary: "需要等待确认。",
          timestamp: "2026-07-14T00:00:06.500Z",
          sequence: 7,
          metadata: { item_id: "reasoning-waiting" },
        },
        {
          id: "run-succeeded-after-reasoning",
          event_type: "run.succeeded",
          kind: "run",
          status: "succeeded",
          title: "运行完成",
          timestamp: "2026-07-14T00:00:07Z",
          sequence: 8,
          metadata: {},
        },
      ],
    });

    const html = renderToStaticMarkup(<ActivitySummary activity={completed} />);

    expect(html).toContain('data-commentary-source="reasoning_summary"');
    expect(html).toContain('data-active="false"');
  });

  it("ends environment preparation as soon as the run starts", () => {
    const modelPending = runActivitySchema.parse({
      run_id: "run-model-pending",
      status: "running",
      started_at: "2026-07-14T00:00:00Z",
      metrics: {},
      items: [
        {
          id: "provisioning",
          event_type: "run.provisioning",
          kind: "run",
          status: "running",
          title: "正在准备运行环境",
          timestamp: "2026-07-14T00:00:00.100Z",
          sequence: 1,
          metadata: {},
        },
        {
          id: "running",
          event_type: "run.running",
          kind: "run",
          status: "running",
          title: "Agent 开始执行",
          timestamp: "2026-07-14T00:00:00.300Z",
          sequence: 2,
          metadata: {},
        },
      ],
    });
    const html = renderToStaticMarkup(<ActivitySummary activity={modelPending} />);

    expect(html).not.toContain("已准备运行环境");
    expect(html).toContain("正在处理");
    expect(html).not.toContain("run-pulse");
    expect(html).not.toContain(">正在准备运行环境<");
  });

  it("renders tasks and tools as flat Codex actions without nested disclosures", () => {
    const html = renderToStaticMarkup(<ActivitySummary activity={activity} />);

    expect(html.match(/<section class="execution-ribbon/g)).toHaveLength(1);
    expect(html).not.toContain("execution-task");
    expect(html).not.toContain("execution-tool");
    expect(html).toContain("fact-checker");
    expect(html).toContain("risk-reviewer");
    expect(html).toContain("运行了 2 个子任务");
    expect(html).toContain("搜索了 1 次内容 · 读取了 1 个文件");
  });

  it("groups adjacent active tools into one quiet action row", () => {
    const html = renderToStaticMarkup(<ActivitySummary activity={activity} />);

    expect(html).toContain("搜索了 1 次内容 · 读取了 1 个文件");
    expect(html.match(/execution-action action-running/g)?.length).toBeGreaterThan(0);
  });

  it("uses one chronological work log for expanded run details", () => {
    const routed = runActivitySchema.parse({
      ...activity,
      items: [
        ...activity.items,
        {
          id: "model-route",
          event_type: "model.route.selected",
          kind: "run",
          status: "succeeded",
          title: "模型路由已选择",
          summary: "claude-sonnet",
          timestamp: "2026-07-14T00:00:07Z",
          sequence: 7,
          metadata: { model: "claude-sonnet" },
        },
      ],
    });
    const html = renderToStaticMarkup(<ActivitySummary activity={routed} />);

    expect(html).toContain('aria-label="运行过程，仅展示可观察事件"');
    expect(html).toContain("运行过程");
    expect(html).not.toContain("运行模型");
    expect(html).not.toContain("<h4>");
  });

  it("shows completed discovery tools directly inside the turn disclosure", () => {
    const completedTools = runActivitySchema.parse({
      ...activity,
      items: [
        ...activity.items,
        {
          id: "tool-one-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          title: "Read 已完成",
          timestamp: "2026-07-14T00:00:07Z",
          sequence: 7,
          metadata: { tool_call_id: "tool-1" },
        },
        {
          id: "tool-two-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          title: "Grep 已完成",
          timestamp: "2026-07-14T00:00:08Z",
          sequence: 8,
          metadata: { tool_call_id: "tool-2" },
        },
        {
          id: "tool-three",
          event_type: "tool.request",
          kind: "tool",
          status: "running",
          title: "调用 Glob",
          timestamp: "2026-07-14T00:00:09Z",
          sequence: 9,
          metadata: {
            tool_call_id: "tool-3",
            name: "Glob",
            arguments: { pattern: "src/**/*.py", path: "src/harness" },
          },
        },
        {
          id: "tool-three-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          title: "Glob 已完成",
          timestamp: "2026-07-14T00:00:10Z",
          sequence: 10,
          metadata: { tool_call_id: "tool-3" },
        },
        {
          id: "run-completed",
          event_type: "run.succeeded",
          kind: "result",
          status: "succeeded",
          title: "任务已完成",
          timestamp: "2026-07-14T00:00:11Z",
          sequence: 11,
          metadata: {},
        },
      ],
    });

    const html = renderToStaticMarkup(<ActivitySummary activity={completedTools} />);

    expect(html).toContain('data-run-id="run-ribbon"');
    expect(html).toContain("查找了 1 次文件 · 搜索了 1 次内容 · 读取了 1 个文件");
    expect(html).toContain("已读取 docs/agent-production-platform-design.md");
    expect(html).toContain("已在 web/harness-console 中搜索“publishDraft|promote”");
    expect(html).toContain("已查找文件 src/**/*.py，范围 src/harness");
    expect(html.match(/execution-action-detail action-completed/g)).toHaveLength(3);
    expect(html).not.toContain('class="execution-tool-batch"');
    expect(html).not.toContain("execution-tool");
  });

  it("keeps grouped command results collapsed until their action is opened", () => {
    const command = "mkdir -p outputs/detection_output outputs/results && python .claude/skills/grid-system/scripts/add_grid.py inputs/original/sample.jpg";
    const withCommand = runActivitySchema.parse({
      ...activity,
      items: [
        activity.items[0],
        {
          id: "glob-request",
          event_type: "tool.request",
          kind: "tool",
          status: "running",
          title: "调用 Glob",
          timestamp: "2026-07-14T00:00:02Z",
          sequence: 2,
          metadata: {
            tool_call_id: "glob-1",
            name: "Glob",
            arguments: { pattern: "inputs/original/*" },
          },
        },
        {
          id: "glob-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          title: "Glob 已完成",
          timestamp: "2026-07-14T00:00:03Z",
          sequence: 3,
          metadata: {
            tool_call_id: "glob-1",
            result_summary: "返回 1 个文件",
            result_preview: "inputs/original/sample.jpg",
          },
        },
        {
          id: "bash-request",
          event_type: "tool.request",
          kind: "tool",
          status: "running",
          title: "调用 Bash",
          timestamp: "2026-07-14T00:00:04Z",
          sequence: 4,
          metadata: {
            tool_call_id: "bash-1",
            name: "Bash",
            arguments: { command },
          },
        },
        {
          id: "bash-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          title: "Bash 已完成",
          timestamp: "2026-07-14T00:00:05Z",
          sequence: 5,
          metadata: {
            tool_call_id: "bash-1",
            result_summary: "退出码 0",
            result_preview: "grid image ready",
          },
        },
      ],
    });

    const html = renderToStaticMarkup(<ActivitySummary activity={withCommand} />);

    expect(html).toContain("查找了 1 次文件 · 运行了 1 个命令");
    expect(html).toContain("已查找文件 inputs/original/*");
    expect(html).toContain(`已运行 ${command.replace("&&", "&amp;&amp;")}`);
    expect(html).toContain("返回 1 个文件");
    expect(html).toContain("退出码 0");
    expect(html).toContain("inputs/original/sample.jpg");
    expect(html).toContain("grid image ready");
    expect(html).toContain('<pre class="execution-action-result"');
    expect(html).toContain("<code>inputs/original/sample.jpg</code>");
    expect(html).toContain('<details class="execution-action action-completed">');
    expect(html).not.toContain(
      '<details class="execution-action action-completed" open',
    );
    expect(html).toContain(
      '<summary class="execution-action-summary">',
    );
    expect(html).toContain('<div class="execution-action-body">');
    expect(html).toContain('role="region"');
    expect(html).toContain('aria-label="已运行 ');
    expect(html).toContain('结果预览"');
    expect(html.match(/<section class="execution-ribbon/g)).toHaveLength(1);
  });

  it("keeps each completed run in its own Codex-style turn group", () => {
    const first = runActivitySchema.parse({
      ...activity,
      run_id: "run-first",
      status: "succeeded",
      items: [
        ...activity.items,
        {
          id: "first-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          title: "Read 已完成",
          timestamp: "2026-07-14T00:00:07Z",
          sequence: 7,
          metadata: { tool_call_id: "tool-1" },
        },
        {
          id: "first-completed",
          event_type: "run.succeeded",
          kind: "result",
          status: "succeeded",
          title: "任务已完成",
          timestamp: "2026-07-14T00:00:08Z",
          sequence: 8,
          metadata: {},
        },
      ],
    });
    const second = runActivitySchema.parse({
      run_id: "run-second",
      status: "succeeded",
      started_at: "2026-07-14T00:01:00Z",
      metrics: {},
      items: [
        {
          id: "second-tool",
          event_type: "tool.request",
          kind: "tool",
          status: "running",
          title: "调用 Bash",
          timestamp: "2026-07-14T00:01:01Z",
          sequence: 1,
          metadata: {
            tool_call_id: "tool-second",
            name: "Bash",
            arguments: { command: "npm test" },
          },
        },
        {
          id: "second-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          title: "Bash 已完成",
          timestamp: "2026-07-14T00:01:02Z",
          sequence: 2,
          metadata: { tool_call_id: "tool-second" },
        },
        {
          id: "second-completed",
          event_type: "run.succeeded",
          kind: "result",
          status: "succeeded",
          title: "任务已完成",
          timestamp: "2026-07-14T00:01:03Z",
          sequence: 3,
          metadata: {},
        },
      ],
    });

    const html = renderToStaticMarkup(
      <>{[first, second].map((item) => (
        <ActivitySummary activity={item} key={item.run_id} />
      ))}</>,
    );

    expect(html).toContain('data-run-id="run-first"');
    expect(html).toContain('data-run-id="run-second"');
    expect(html).toContain("搜索了 1 次内容 · 读取了 1 个文件");
    expect(html).toContain("已运行 npm test");
  });

  it.each([
    ["approval.requested", "tool", "waiting", "等待审批"],
    ["run.failed", "error", "failed", "处理失败"],
  ] as const)(
    "keeps the %s state explicit in the collapsed line",
    (eventType, kind, status, label) => {
      const state = runActivitySchema.parse({
        ...activity,
        status,
        items: [
          ...activity.items,
          {
            id: `state-${status}`,
            event_type: eventType,
            kind,
            status,
            title: label,
            timestamp: "2026-07-14T00:00:07Z",
            sequence: 7,
            metadata:
              eventType === "approval.requested"
                ? { approval_id: "approval-1", tool_call_id: "tool-1" }
                : {},
          },
        ],
      });
      const html = renderToStaticMarkup(<ActivitySummary activity={state} />);
      const phase =
        eventType === "approval.requested" ? "waiting_approval" : "failed";

      expect(html).toContain(label);
      if (phase === "waiting_approval") {
        expect(html).toContain(
          `<section class="execution-ribbon phase-${phase}" aria-label="执行进度 run-ribbon" data-run-id="run-ribbon" data-response-started="false" data-open="true"`,
        );
      } else {
        expect(html).toContain(
          `<section class="execution-ribbon phase-${phase}" aria-label="执行进度 run-ribbon" data-run-id="run-ribbon" data-response-started="false" data-open="true"`,
        );
        expect(html).toContain('aria-expanded="true"');
        expect(html).toContain('aria-label="失败定位"');
        expect(html).toContain("2 个工具动作");
        expect(html).toContain("搜索了 1 次内容 · 读取了 1 个文件");
      }
    },
  );

  it("marks successful runs as a visually secondary state", () => {
    const completed = runActivitySchema.parse({
      ...activity,
      status: "succeeded",
      items: [
        ...activity.items,
        {
          id: "run-completed",
          event_type: "run.succeeded",
          kind: "result",
          status: "succeeded",
          title: "任务已完成",
          timestamp: "2026-07-14T00:00:07Z",
          sequence: 7,
          metadata: {},
        },
      ],
    });
    const html = renderToStaticMarkup(<ActivitySummary activity={completed} />);

    expect(html).toContain("phase-completed");
    expect(html).toContain("搜索了 1 次内容 · 读取了 1 个文件");
  });
});
