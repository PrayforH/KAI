export type RunStatus =
  | "idle"
  | "queued"
  | "provisioning"
  | "running"
  | "waiting_approval"
  | "cancelling"
  | "cancelled"
  | "succeeded"
  | "failed"
  | "timed_out"
  | "rejected";

export interface User {
  id: string;
  name: string;
  email: string;
  role?: string;
}

export interface Agent {
  name: string;
  version: string;
  displayName: string;
  modelRoute?: string;
  model?: string;
  ownerUserId: string;
  scope: "personal" | "team";
  spaceId?: string;
  spaceName?: string;
  canChat: boolean;
}

export interface Approval {
  id: string;
  runId: string;
  toolCallId: string;
  status: string;
  reason: string;
  toolName?: string;
  arguments?: Record<string, unknown>;
  risk?: string;
}

export interface Artifact {
  id: string;
  runId: string;
  name: string;
  mediaType: string;
  sizeBytes?: number;
  status?: string;
}

export interface ActivityItem {
  id: string;
  kind: "run" | "analysis" | "tool" | "subagent" | "artifact" | "result" | "error" | string;
  status: string;
  title: string;
  summary?: string;
  timestamp?: string;
}

export interface Activity {
  status: string;
  items: ActivityItem[];
  metrics: Record<string, unknown>;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: string;
  result?: string;
  status: "running" | "complete" | "error";
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  createdAt?: string;
  toolCalls?: ToolCall[];
  artifacts?: Artifact[];
  activity?: Activity;
}

export interface Thread {
  id: string;
  sessionId: string;
  title: string;
  agentName: string;
  agentVersion: string;
  ownerUserId: string;
  spaceId?: string;
  status: RunStatus;
  runId?: string;
  createdAt: string;
  updatedAt: string;
  archived?: boolean;
  approval?: Approval;
}

export interface ContextOverview {
  sessionId: string;
  percentage?: number;
  totalTokens?: number;
  maxTokens?: number;
  level?: string;
  recommendedAction?: string;
  previousSessionCount: number;
  rebaseSupported: boolean;
  rollbackSupported: boolean;
}

export interface InputArtifact {
  id: string;
  name: string;
  mediaType: string;
  sizeBytes?: number;
}

export type KaiEvent =
  | { kind: "run.started"; clientRunId: string }
  | { kind: "run.finished" }
  | { kind: "run.error"; code?: string; message: string }
  | { kind: "message.started"; messageId: string }
  | { kind: "message.delta"; messageId: string; delta: string }
  | { kind: "message.finished"; messageId: string }
  | { kind: "tool.started"; toolCallId: string; name: string }
  | { kind: "tool.arguments"; toolCallId: string; delta: string }
  | { kind: "tool.finished"; toolCallId: string }
  | { kind: "tool.result"; toolCallId: string; content: string }
  | { kind: "activity.snapshot"; activity: Activity }
  | { kind: "activity.delta"; patch: JsonPatch[] };

export interface JsonPatch {
  op: "add" | "replace" | "remove";
  path: string;
  value?: unknown;
}

export interface RunHandle {
  threadId: string;
  clientRunId: string;
  serverRunId?: string;
  lastEventId?: string;
}

export interface StreamCallbacks {
  onHandle?: (handle: RunHandle) => void;
  onEvent: (event: KaiEvent, eventId?: string) => void;
  onTransportState?: (state: "connected" | "recovering") => void;
}
