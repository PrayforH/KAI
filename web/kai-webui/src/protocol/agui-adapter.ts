import type { Activity, JsonPatch, KaiEvent } from "./types";

type WireEvent = Record<string, unknown> & { type?: string };

export function parseSseFrame(frame: string): { id?: string; event?: WireEvent } {
  let id: string | undefined;
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith("id:")) id = line.slice(3).trim();
    if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
  }
  if (!data.length) return { id };
  return { id, event: JSON.parse(data.join("\n")) as WireEvent };
}

export async function readSse(
  response: Response,
  onFrame: (id: string | undefined, event: WireEvent) => void,
  signal?: AbortSignal,
) {
  if (!response.ok || !response.body) throw new Error(`实时连接失败 (${response.status})`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    if (signal?.aborted) throw signal.reason ?? new DOMException("已取消", "AbortError");
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const parsed = parseSseFrame(frame);
      if (parsed.event) onFrame(parsed.id, parsed.event);
    }
    if (done) break;
  }
}

export function fromAgui(event: WireEvent): KaiEvent | undefined {
  const type = String(event.type ?? "");
  switch (type) {
    case "RUN_STARTED":
      return { kind: "run.started", clientRunId: String(event.runId ?? "") };
    case "RUN_FINISHED":
      return { kind: "run.finished" };
    case "RUN_ERROR":
      return { kind: "run.error", code: text(event.code), message: text(event.message) || "运行失败" };
    case "TEXT_MESSAGE_START":
      return { kind: "message.started", messageId: String(event.messageId) };
    case "TEXT_MESSAGE_CONTENT":
      return { kind: "message.delta", messageId: String(event.messageId), delta: String(event.delta ?? "") };
    case "TEXT_MESSAGE_END":
      return { kind: "message.finished", messageId: String(event.messageId) };
    case "TOOL_CALL_START":
      return { kind: "tool.started", toolCallId: String(event.toolCallId), name: String(event.toolCallName ?? "工具") };
    case "TOOL_CALL_ARGS":
      return { kind: "tool.arguments", toolCallId: String(event.toolCallId), delta: String(event.delta ?? "") };
    case "TOOL_CALL_END":
      return { kind: "tool.finished", toolCallId: String(event.toolCallId) };
    case "TOOL_CALL_RESULT":
      return { kind: "tool.result", toolCallId: String(event.toolCallId), content: String(event.content ?? "") };
    case "ACTIVITY_SNAPSHOT":
      if (event.activityType !== "harness.run.v1") return undefined;
      return { kind: "activity.snapshot", activity: normalizeActivity(event.content) };
    case "ACTIVITY_DELTA":
      if (event.activityType !== "harness.run.v1" || !Array.isArray(event.patch)) return undefined;
      return { kind: "activity.delta", patch: event.patch as JsonPatch[] };
    default:
      return undefined;
  }
}

function text(value: unknown) {
  return typeof value === "string" ? value : undefined;
}

function normalizeActivity(value: unknown): Activity {
  const row = object(value);
  return {
    status: String(row.status ?? "running"),
    items: Array.isArray(row.items)
      ? row.items.map((item, index) => {
          const record = object(item);
          return {
            id: String(record.id ?? `activity-${index}`),
            kind: String(record.kind ?? "run"),
            status: String(record.status ?? "running"),
            title: String(record.title ?? record.event_type ?? "运行步骤"),
            summary: text(record.summary),
            timestamp: text(record.timestamp),
          };
        })
      : [],
    metrics: object(row.metrics),
  };
}

function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

export function applyActivityPatch(activity: Activity | undefined, patch: JsonPatch[]): Activity | undefined {
  if (!activity) return activity;
  const next = structuredClone(activity) as unknown as Record<string, unknown>;
  for (const operation of patch) {
    const path = operation.path.split("/").slice(1).map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
    if (!path.length) continue;
    let target: Record<string, unknown> | unknown[] = next;
    for (const segment of path.slice(0, -1)) {
      const key = Array.isArray(target) ? Number(segment) : segment;
      const child = target[key as keyof typeof target];
      if (!child || typeof child !== "object") break;
      target = child as Record<string, unknown> | unknown[];
    }
    const last = path.at(-1)!;
    if (Array.isArray(target) && operation.op === "add" && last === "-") {
      target.push(operation.value);
      continue;
    }
    const key = Array.isArray(target) ? Number(last) : last;
    if (operation.op === "remove") {
      if (Array.isArray(target)) target.splice(Number(key), 1);
      else delete target[key as keyof typeof target];
    } else {
      target[key as keyof typeof target] = operation.value as never;
    }
  }
  return next as unknown as Activity;
}
