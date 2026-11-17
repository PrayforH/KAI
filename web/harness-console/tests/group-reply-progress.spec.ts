import { describe, expect, it } from "vitest";

import {
  GROUP_REPLY_FALLBACK_TEXT,
  GROUP_REPLY_STAGE_LABEL,
  isTerminalEvent,
  nextGroupReplyStage,
  stageFromActivity,
  takeSseFrames,
} from "../src/lib/group-reply-progress";

function activity(types: string[]) {
  return {
    run_id: "run-1",
    status: "running",
    items: types.map((event_type, index) => ({
      id: `item-${index}`,
      event_type,
      metadata: { tool_call_id: "call-1" },
    })),
  };
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
    expect(stageFromActivity(activity(["run.started", "tool.request"]))).toBe("tool");
  });

  it("活动快照：工具已返回、还没有正文时回到正在思考", () => {
    expect(
      stageFromActivity(activity(["run.started", "tool.request", "tool.result"])),
    ).toBe("thinking");
  });

  it("活动快照：正文开始后显示正在整理回复", () => {
    expect(
      stageFromActivity(activity(["run.started", "message.delta", "message.delta"])),
    ).toBe("writing");
  });

  it("活动增量按条目推进阶段", () => {
    let stage = nextGroupReplyStage(undefined, {
      type: "ACTIVITY_DELTA",
      activityType: "harness.run.v1",
      patch: [{ op: "add", path: "/items/-", value: { event_type: "tool.request" } }],
    });
    expect(stage).toBe("tool");
    stage = nextGroupReplyStage(stage, {
      type: "ACTIVITY_DELTA",
      activityType: "harness.run.v1",
      patch: [{ op: "add", path: "/items/-", value: { event_type: "tool.result" } }],
    });
    expect(stage).toBe("thinking");
    stage = nextGroupReplyStage(stage, {
      type: "ACTIVITY_DELTA",
      activityType: "harness.run.v1",
      patch: [{ op: "add", path: "/items/-", value: { event_type: "message.delta" } }],
    });
    expect(stage).toBe("writing");
  });

  it("没有活动投影时按 AG-UI 事件兜底", () => {
    expect(nextGroupReplyStage(undefined, { type: "RUN_STARTED" })).toBe("thinking");
    expect(nextGroupReplyStage("thinking", { type: "TOOL_CALL_START" })).toBe("tool");
    expect(nextGroupReplyStage("tool", { type: "TEXT_MESSAGE_CONTENT" })).toBe("writing");
    expect(nextGroupReplyStage("writing", { type: "TOOL_CALL_RESULT" })).toBe("thinking");
  });

  it("无关事件不改变阶段", () => {
    expect(nextGroupReplyStage("tool", { type: "STATE_SNAPSHOT" })).toBe("tool");
    expect(nextGroupReplyStage(undefined, { type: "ACTIVITY_DELTA", patch: [] })).toBeUndefined();
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
