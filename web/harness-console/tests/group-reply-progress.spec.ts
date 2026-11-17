import { describe, expect, it } from "vitest";

import {
  GROUP_REPLY_FALLBACK_TEXT,
  GROUP_REPLY_PROGRESS_EMPTY,
  GROUP_REPLY_STAGE_LABEL,
  isTerminalEvent,
  nextGroupReplyProgress,
  progressFromActivity,
  stageFromActivity,
  takeSseFrames,
  type GroupReplyProgress,
  type GroupReplyStreamEvent,
} from "../src/lib/group-reply-progress";

type Item = { event_type: string; tool_call_id?: string };

function item(value: Item) {
  return {
    event_type: value.event_type,
    metadata: value.tool_call_id ? { tool_call_id: value.tool_call_id } : {},
  };
}

function activity(items: Item[]) {
  return {
    run_id: "run-1",
    status: "running",
    items: items.map((value, index) => ({ id: `item-${index}`, ...item(value) })),
  };
}

function delta(value: Item) {
  return {
    type: "ACTIVITY_DELTA",
    activityType: "harness.run.v1",
    patch: [{ op: "add", path: "/items/-", value: item(value) }],
  };
}

function fold(events: GroupReplyStreamEvent[]): GroupReplyProgress {
  return events.reduce<GroupReplyProgress>(
    (state, event) => nextGroupReplyProgress(state, event),
    GROUP_REPLY_PROGRESS_EMPTY,
  );
}

describe("群回复进度阶段", () => {
  it("三档文案固定且不含工具名", () => {
    expect(GROUP_REPLY_STAGE_LABEL).toEqual({
      thinking: "正在思考",
      tool: "正在调用工具",
      writing: "正在整理回复",
    });
    expect(GROUP_REPLY_FALLBACK_TEXT).toBe("正在回复…");
  });

  it("活动快照：未完成的工具请求显示正在调用工具", () => {
    expect(stageFromActivity(activity([{ event_type: "run.started" }, { event_type: "tool.request" }])))
      .toBe("tool");
  });

  it("活动快照：工具已返回、还没有正文时回到正在思考", () => {
    expect(
      stageFromActivity(activity([
        { event_type: "run.started" },
        { event_type: "tool.request", tool_call_id: "a" },
        { event_type: "tool.result", tool_call_id: "a" },
      ])),
    ).toBe("thinking");
  });

  it("活动快照：正文开始后显示正在整理回复", () => {
    expect(
      stageFromActivity(activity([
        { event_type: "run.started" },
        { event_type: "message.delta" },
        { event_type: "message.delta" },
      ])),
    ).toBe("writing");
  });

  it("并行工具：还有一个没返回就保持正在调用工具", () => {
    const progress = progressFromActivity(activity([
      { event_type: "tool.request", tool_call_id: "a" },
      { event_type: "tool.request", tool_call_id: "b" },
      { event_type: "tool.result", tool_call_id: "a" },
    ]));
    expect(progress.stage).toBe("tool");
    expect(progress.pendingToolCalls).toEqual(["b"]);

    expect(
      progressFromActivity(activity([
        { event_type: "tool.request", tool_call_id: "a" },
        { event_type: "tool.request", tool_call_id: "b" },
        { event_type: "tool.result", tool_call_id: "b" },
        { event_type: "tool.result", tool_call_id: "a" },
      ])).stage,
    ).toBe("thinking");
  });

  it("增量跨帧保留并行工具集合，任一返回不掉档", () => {
    const progress = fold([
      delta({ event_type: "tool.request", tool_call_id: "a" }),
      delta({ event_type: "tool.request", tool_call_id: "b" }),
      { type: "STATE_SNAPSHOT" },
      delta({ event_type: "tool.result", tool_call_id: "a" }),
    ]);
    expect(progress.stage).toBe("tool");

    // 另一个工具返回后，才回到思考；随后正文到达才是整理回复。
    expect(
      fold([
        delta({ event_type: "tool.request", tool_call_id: "a" }),
        delta({ event_type: "tool.request", tool_call_id: "b" }),
        delta({ event_type: "tool.result", tool_call_id: "a" }),
        delta({ event_type: "tool.result", tool_call_id: "b" }),
      ]).stage,
    ).toBe("thinking");
  });

  it("增量按条目推进阶段", () => {
    let progress = nextGroupReplyProgress(
      GROUP_REPLY_PROGRESS_EMPTY,
      delta({ event_type: "tool.request", tool_call_id: "a" }),
    );
    expect(progress.stage).toBe("tool");
    progress = nextGroupReplyProgress(progress, delta({ event_type: "tool.result", tool_call_id: "a" }));
    expect(progress.stage).toBe("thinking");
    progress = nextGroupReplyProgress(progress, delta({ event_type: "message.delta" }));
    expect(progress.stage).toBe("writing");
  });

  it("过程事件不误报成工具调用", () => {
    // 运行时初始化 / 上下文压缩 / 子 Agent 阶段 / 审批等待都只是“还在处理”。
    for (const event_type of [
      "runtime.system",
      "context.compaction.started",
      "subagent.started",
      "subagent.completed",
      "approval.requested",
    ]) {
      const progress = fold([delta({ event_type })]);
      expect(progress.stage).not.toBe("tool");
      expect(progress.stage).toBe("thinking");
    }
    // 产物、界面回执、策略解析不表达进行中的工作，不改变阶段。
    expect(fold([delta({ event_type: "artifact.ready" })]).stage).toBeUndefined();
    expect(fold([delta({ event_type: "ui.assistant_control" })]).stage).toBeUndefined();
    expect(fold([delta({ event_type: "policy.resolved" })]).stage).toBeUndefined();
  });

  it("工具在跑时，过程事件不会把工具档改掉", () => {
    const progress = fold([
      delta({ event_type: "tool.request", tool_call_id: "a" }),
      delta({ event_type: "runtime.system" }),
      delta({ event_type: "message.delta" }),
    ]);
    expect(progress.stage).toBe("tool");
  });

  it("没有活动投影时按 AG-UI 事件兜底", () => {
    expect(fold([{ type: "RUN_STARTED" }]).stage).toBe("thinking");
    expect(fold([{ type: "TOOL_CALL_START", toolCallId: "a" }]).stage).toBe("tool");
    expect(fold([{ type: "TEXT_MESSAGE_CONTENT" }]).stage).toBe("writing");
  });

  it("TOOL_CALL_END 只是参数帧结束，不结束工具档", () => {
    const progress = fold([
      { type: "TOOL_CALL_START", toolCallId: "a" },
      { type: "TOOL_CALL_ARGS", toolCallId: "a" },
      { type: "TOOL_CALL_END", toolCallId: "a" },
    ]);
    expect(progress.stage).toBe("tool");
    expect(progress.pendingToolCalls).toEqual(["a"]);

    // 真正的完成信号是 TOOL_CALL_RESULT，且要按 id 匹配。
    expect(
      fold([
        { type: "TOOL_CALL_START", toolCallId: "a" },
        { type: "TOOL_CALL_END", toolCallId: "a" },
        { type: "TOOL_CALL_START", toolCallId: "b" },
        { type: "TOOL_CALL_RESULT", toolCallId: "a" },
      ]).stage,
    ).toBe("tool");
  });

  it("标准事件与活动投影重复到达时保持幂等", () => {
    const progress = fold([
      { type: "TOOL_CALL_START", toolCallId: "a" },
      delta({ event_type: "tool.request", tool_call_id: "a" }),
      { type: "TOOL_CALL_END", toolCallId: "a" },
      delta({ event_type: "tool.result", tool_call_id: "a" }),
    ]);
    expect(progress.stage).toBe("thinking");
    expect(progress.pendingToolCalls).toEqual([]);
  });

  it("缺少工具调用 id 时不会卡在工具档", () => {
    const progress = fold([
      delta({ event_type: "tool.request" }),
      delta({ event_type: "tool.result" }),
    ]);
    expect(progress.stage).toBe("thinking");
    expect(progress.pendingToolCalls).toEqual([]);
  });

  it("真实后端审批帧序列全程不出现工具档", () => {
    // mapper.py:157-176 / :178-190：审批请求是一帧标准工具调用，
    // 审批结论才补 TOOL_CALL_RESULT，工具名为 harness_request_approval。
    const events: GroupReplyStreamEvent[] = [
      { type: "RUN_STARTED" },
      { type: "TOOL_CALL_START", toolCallId: "harness-approval-a1", toolCallName: "harness_request_approval" },
      { type: "TOOL_CALL_ARGS", toolCallId: "harness-approval-a1" },
      { type: "TOOL_CALL_END", toolCallId: "harness-approval-a1" },
      delta({ event_type: "approval.requested", tool_call_id: "call-1" }),
      delta({ event_type: "approval.approved" }),
      { type: "TOOL_CALL_RESULT", toolCallId: "harness-approval-a1" },
      delta({ event_type: "message.delta" }),
    ];
    const stages: Array<string | undefined> = [];
    let progress = GROUP_REPLY_PROGRESS_EMPTY;
    for (const event of events) {
      progress = nextGroupReplyProgress(progress, event);
      stages.push(progress.stage);
    }
    expect(stages).not.toContain("tool");
    expect(progress.pendingToolCalls).toEqual([]);
    expect(progress.stage).toBe("writing");
  });

  it("审批条目把卡住的工具从 pending 摘掉，其余并行工具仍算执行中", () => {
    // 真实工具在等审批：approval.requested 的 metadata.tool_call_id 指向它。
    expect(
      fold([
        delta({ event_type: "tool.request", tool_call_id: "call-1" }),
        delta({ event_type: "approval.requested", tool_call_id: "call-1" }),
      ]).stage,
    ).toBe("thinking");

    // 另一个工具确实还在跑：审批不掩盖它。
    const parallel = fold([
      delta({ event_type: "tool.request", tool_call_id: "call-1" }),
      delta({ event_type: "tool.request", tool_call_id: "call-2" }),
      delta({ event_type: "approval.requested", tool_call_id: "call-1" }),
    ]);
    expect(parallel.stage).toBe("tool");
    expect(parallel.pendingToolCalls).toEqual(["call-2"]);
  });

  it("审批工具帧不进入 pending，真实工具帧照旧进入", () => {
    const approval = fold([
      { type: "TOOL_CALL_START", toolCallId: "harness-approval-a1", toolCallName: "harness_request_approval" },
    ]);
    expect(approval.stage).toBe("thinking");
    expect(approval.pendingToolCalls).toEqual([]);

    const real = fold([
      { type: "TOOL_CALL_START", toolCallId: "a", toolCallName: "assistant_group_update_profile" },
    ]);
    expect(real.stage).toBe("tool");
    expect(real.pendingToolCalls).toEqual(["a"]);
  });

  it("无关事件不改变阶段", () => {
    expect(fold([delta({ event_type: "artifact.ready" }), { type: "STATE_SNAPSHOT" }]).stage)
      .toBeUndefined();
    const state: GroupReplyProgress = { stage: "tool", pendingToolCalls: [] };
    expect(nextGroupReplyProgress(state, { type: "STATE_SNAPSHOT" })).toBe(state);
  });

  it("只把 RUN_FINISHED / RUN_ERROR 当作终态", () => {
    expect(isTerminalEvent({ type: "RUN_FINISHED" })).toBe(true);
    expect(isTerminalEvent({ type: "RUN_ERROR" })).toBe(true);
    expect(isTerminalEvent({ type: "RUN_STARTED" })).toBe(false);
  });
});

describe("SSE 帧解析", () => {
  it("按空行切帧并保留未完成部分", () => {
    const { frames, rest } = takeSseFrames(
      'id: 3:1\ndata: {"type":"TOOL_CALL_START"}\n\nid: 4\ndata: {"type":"RUN_STARTED"}\n\ndata: {"ty',
    );
    expect(frames).toHaveLength(2);
    expect(frames[0]).toEqual({ id: "3:1", data: '{"type":"TOOL_CALL_START"}' });
    expect(frames[1].id).toBe("4");
    expect(rest).toBe('data: {"ty');
  });

  it("忽略只有注释心跳的帧", () => {
    const { frames } = takeSseFrames(": keep-alive\n\n");
    expect(frames).toHaveLength(0);
  });
});
