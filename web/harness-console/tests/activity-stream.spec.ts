import { afterEach, expect, it, vi } from "vitest";
import { Subject } from "rxjs";
import { EventType, type BaseEvent, type ActivitySnapshotEvent } from "@ag-ui/client";
import { runActivitySchema } from "../src/lib/activity-schema";
import { activityStore } from "../src/lib/activity-store";
import { compactActivityStream } from "../src/lib/activity-stream";
const snapshot = (): ActivitySnapshotEvent => ({ type: EventType.ACTIVITY_SNAPSHOT, replace: true, messageId: "a", activityType: "harness.run.v1", content: { run_id: "r", status: "running", started_at: "2026-09-20T00:00:00Z", items: [], metrics: {} } });
const delta = (sequence: number, text = "片段", event_type = "reasoning.delta", item_id = "thought") => ({
  type: EventType.ACTIVITY_DELTA, messageId: "a", activityType: "harness.run.v1",
  patch: [{ op: "add", path: "/items/-", value: { id: `e${sequence}`, sequence, event_type, kind: "analysis", title: "思考", status: "succeeded", summary: text, timestamp: "2026-09-20T00:00:00Z", metadata: { item_id } } }],
});
function stream() { const source = new Subject<BaseEvent>(); const events: BaseEvent[] = []; const subscription = compactActivityStream(source).subscribe(event => events.push(event)); source.next(snapshot()); return { source, events, subscription }; }
afterEach(() => vi.useRealTimers());
it("bounds a 7,421-token burst to one row and preserves text and prior snapshots", () => {
  vi.useFakeTimers(); const { source, events } = stream();
  for (let i = 1; i <= 7421; i++) source.next(delta(i));
  expect(events).toHaveLength(1); vi.advanceTimersByTime(50); expect(events).toHaveLength(2);
  const first = events[1] as ActivitySnapshotEvent;
  expect(first.content.items).toHaveLength(1);
  expect(first.content.items[0]).toMatchObject({ id: "e1", sequence: 1, summary: "片段".repeat(7421) });
  source.next(delta(7422, "尾部")); source.complete();
  expect((events[2] as ActivitySnapshotEvent).content.items[0].summary).toBe("片段".repeat(7421) + "尾部");
  expect(first.content.items[0].summary).toBe("片段".repeat(7421));
});
it("flushes before tools and artifacts and preserves separate thinking blocks", () => {
  vi.useFakeTimers(); const { source, events } = stream(); source.next(delta(1, "第一段"));
  const tool = { type: EventType.TOOL_CALL_START, toolCallId: "tool", toolCallName: "Read" }; source.next(tool);
  expect(events[2]).toBe(tool); expect((events[1] as ActivitySnapshotEvent).content.items[0].summary).toBe("第一段");
  source.next(delta(2, "工具", "tool.request")); source.next(delta(3, "第二段", "reasoning.delta", "thought-2"));
  const artifact = { type: EventType.TOOL_CALL_START, toolCallId: "artifact", toolCallName: "harness_present_artifact" }; source.next(artifact);
  source.next({ type: EventType.RUN_FINISHED, threadId: "t", runId: "r" });
  expect(events.at(-2)).toBe(artifact);
  expect((events.at(-3) as ActivitySnapshotEvent).content.items.map((i: {summary: string}) => i.summary)).toEqual(["第一段", "工具", "第二段"]);
});
it("publishes terminal status and escaped metric paths immediately", () => {
  vi.useFakeTimers(); const { source, events } = stream(); source.next(delta(1));
  source.next({ type: EventType.ACTIVITY_DELTA, messageId: "a", activityType: "harness.run.v1", patch: [
    {op: "replace", path: "/status", value: "succeeded"}, {op: "add", path: "/metrics/total~1tokens", value: 42},
  ] });
  expect((events.at(-1) as ActivitySnapshotEvent).content).toMatchObject({ status: "succeeded", metrics: { "total/tokens": 42 } });
  expect(vi.getTimerCount()).toBe(0);
});
it("passes other activities unchanged and cancels scheduled work on unsubscribe", () => {
  vi.useFakeTimers(); const { source, events, subscription } = stream();
  const other = {...delta(1), activityType: "other", messageId: "other"}; source.next(other); expect(events.at(-1)).toBe(other);
  source.next(delta(2)); subscription.unsubscribe(); vi.runAllTimers(); expect(events).toHaveLength(2); expect(vi.getTimerCount()).toBe(0);
});
it("flushes the last thought on transport failure", () => {
  vi.useFakeTimers(); const source = new Subject<BaseEvent>(); const received: BaseEvent[] = []; const failed = vi.fn();
  compactActivityStream(source).subscribe({next: event => received.push(event), error: failed});
  source.next(snapshot()); source.next(delta(1, "最后片段")); source.error(new Error("offline"));
  expect((received.at(-1) as ActivitySnapshotEvent).content.items[0].summary).toBe("最后片段");
  expect(failed).toHaveBeenCalledOnce(); expect(vi.getTimerCount()).toBe(0);
});
it("does not append replayed token events after compaction or a reconnect snapshot", () => {
  vi.useFakeTimers(); const { source, events } = stream();
  source.next(delta(1,"准备")); source.next(delta(2,"完成")); vi.advanceTimersByTime(50);
  source.next(delta(2,"完成")); vi.advanceTimersByTime(50);
  expect((events.at(-1) as ActivitySnapshotEvent).content.items[0].summary).toBe("准备完成");
  const restored = {...snapshot(), replace:false, content:{...snapshot().content,items:[{...delta(1,"准备完成").patch[0].value, sequence:1}]}};
  source.next(restored);
  expect((events.at(-1) as ActivitySnapshotEvent).replace).toBe(true);
  source.next(delta(1,"准备完成")); source.next(delta(3,"，继续")); source.complete();
  expect((events.at(-1) as ActivitySnapshotEvent).content.items[0].summary).toBe("准备完成，继续");
});
it("retains intentionally repeated words delivered by distinct events", () => {
 const {source,events}=stream();source.next(delta(1,"检查"));source.next(delta(2,"检查"));source.complete();
 expect((events.at(-1) as ActivitySnapshotEvent).content.items[0].summary).toBe("检查检查");
});

it("replaces token fragments with a restored compact row instead of rendering progress twice", () => {
 activityStore.clear(); const source=new Subject<BaseEvent>();
 compactActivityStream(source).subscribe(event=>{if(event.type===EventType.ACTIVITY_SNAPSHOT){const e=event as ActivitySnapshotEvent;activityStore.publish(runActivitySchema.parse(e.content),undefined,{replaceRun:e.replace===true});}});
 source.next(snapshot());source.next(delta(1,"工作区与 Node 均可用。", "message.delta"));source.next(delta(2,"继续构建。", "message.delta"));
 source.next({...snapshot(),replace:false,content:{...snapshot().content,items:[{...delta(1,"工作区与 Node 均可用。继续构建。", "message.delta").patch[0].value,id:"restored-progress"}]}});
 expect(activityStore.getViewSnapshot()?.items).toHaveLength(1);
 expect(activityStore.getViewSnapshot()?.items[0].summary).toBe("工作区与 Node 均可用。继续构建。");
 source.complete();activityStore.clear();
});
