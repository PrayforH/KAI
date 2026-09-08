import { fromAgui, readSse } from "./agui-adapter";
import { randomId } from "./id";
import type {
  Agent,
  Approval,
  Artifact,
  ContextOverview,
  InputArtifact,
  Message,
  RunHandle,
  StreamCallbacks,
  Thread,
  User,
} from "./types";

const API_ROOT = "/api/v1";
const ACCESS_KEY = "kai_auth_token";
const REFRESH_KEY = "kai_refresh_token";
const USER_KEY = "kai_user";

type Json = Record<string, unknown>;

export const authStore = {
  token: () => localStorage.getItem(ACCESS_KEY),
  refreshToken: () => localStorage.getItem(REFRESH_KEY),
  user: (): User | null => parseStored<User>(USER_KEY),
  save(payload: Json) {
    const raw = object(payload.user);
    const membership = object(payload.membership);
    const user = { id: String(raw.user_id), name: String(raw.display_name || raw.email), email: String(raw.email), role: text(membership.role) };
    localStorage.setItem(ACCESS_KEY, String(payload.access_token));
    localStorage.setItem(REFRESH_KEY, String(payload.refresh_token));
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    return user;
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
  },
};

let refreshing: Promise<boolean> | undefined;

async function refreshAccess() {
  const refreshToken = authStore.refreshToken();
  if (!refreshToken) return false;
  refreshing ??= fetch(`${API_ROOT}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  }).then(async (response) => {
    if (!response.ok) return false;
    authStore.save(await response.json() as Json);
    return true;
  }).finally(() => { refreshing = undefined; });
  return refreshing;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers = new Headers(init.headers);
  const token = authStore.token();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  if (response.status === 401 && retry && await refreshAccess()) return request<T>(path, init, false);
  if (response.status === 401) {
    authStore.clear();
    window.dispatchEvent(new Event("kai:unauthorized"));
  }
  if (!response.ok) throw await apiError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

async function apiError(response: Response) {
  let message = `请求失败 (${response.status})`;
  try {
    const body = await response.json() as Json;
    const detail = body.detail;
    message = typeof detail === "string" ? detail : text(object(detail).message) || text(body.message) || message;
  } catch { /* HTTP fallback is enough. */ }
  return new Error(message);
}

async function authorizedFetch(path: string, init: RequestInit, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = authStore.token();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers });
  if (response.status === 401 && retry && await refreshAccess()) return authorizedFetch(path, init, false);
  if (response.status === 401) {
    authStore.clear();
    window.dispatchEvent(new Event("kai:unauthorized"));
  }
  if (!response.ok) throw await apiError(response);
  return response;
}

export const kaiClient = {
  async login(email: string, password: string) {
    const payload = await request<Json>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }, false);
    return authStore.save(payload);
  },
  async me() {
    const payload = await request<Json>("/auth/me");
    const user = object(payload.user);
    const membership = object(payload.membership);
    return { id: String(user.user_id), name: String(user.display_name || user.email), email: String(user.email), role: text(membership.role) } satisfies User;
  },
  async logout() {
    const refreshToken = authStore.refreshToken();
    if (refreshToken) await request<void>("/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) }).catch(() => undefined);
    authStore.clear();
  },
  async agents(): Promise<Agent[]> {
    const rows = await request<Json[]>("/agents");
    return rows.filter((row) => row.can_chat !== false).map((row) => ({
      name: String(row.name), version: String(row.version), displayName: String(row.display_name || row.name),
      modelRoute: text(row.model_route), model: text(row.model), ownerUserId: String(row.owner_user_id),
      scope: row.scope === "team" ? "team" : "personal", spaceId: text(row.space_id), spaceName: text(row.space_name), canChat: row.can_chat !== false,
    }));
  },
  async modelRoutes(): Promise<Array<{ id: string; model: string; enabled: boolean }>> {
    const body = await request<Json>("/studio/capabilities");
    const rows = Array.isArray(body.modelRoutes) ? body.modelRoutes as Json[] : [];
    return rows.map((row) => ({
      id: String(row.routeId),
      model: Array.isArray(row.models) ? String(row.models[0] || row.routeId) : String(row.routeId),
      enabled: row.enabled !== false,
    }));
  },
  async threads(archived = false): Promise<Thread[]> {
    const rows = await request<Json[]>(`/agui/threads?limit=100&archived=${archived}`);
    return rows.map(toThread);
  },
  async history(threadId: string): Promise<{ status: string; runId?: string; messages: Message[] }> {
    const body = await request<Json>(`/agui/threads/${encodeURIComponent(threadId)}/history`);
    const rows = Array.isArray(body.messages) ? body.messages as Json[] : [];
    const tools = new Map<string, { result?: string }>();
    for (const row of rows) if (row.role === "tool" && row.toolCallId) tools.set(String(row.toolCallId), { result: String(row.content ?? "") });
    return {
      status: String(body.status ?? "idle"), runId: text(body.run_id),
      messages: rows.filter((row) => row.role !== "tool").map((row, index) => historyMessage(row, index, tools)),
    };
  },
  async context(threadId: string): Promise<ContextOverview> {
    const body = await request<Json>(`/agui/threads/${encodeURIComponent(threadId)}/context`);
    const window = object(body.window);
    return {
      sessionId: String(body.session_id), percentage: number(window.percentage), totalTokens: number(window.total_tokens), maxTokens: number(window.max_tokens),
      level: text(window.level), recommendedAction: text(window.recommended_action), previousSessionCount: number(body.previous_session_count) ?? 0,
      rebaseSupported: body.rebase_supported === true, rollbackSupported: body.rollback_supported === true,
    };
  },
  archive: (threadId: string, archived: boolean) => request(`/agui/threads/${encodeURIComponent(threadId)}`, { method: "PATCH", body: JSON.stringify({ archived }) }),
  rebaseContext: (threadId: string) => request(`/agui/threads/${encodeURIComponent(threadId)}/context/rebase`, { method: "POST" }),
  rollbackContext: (threadId: string) => request(`/agui/threads/${encodeURIComponent(threadId)}/context/rebase/rollback`, { method: "POST" }),
  async upload(file: File): Promise<InputArtifact> {
    const form = new FormData(); form.append("file", file);
    const row = await request<Json>("/input-artifacts", { method: "POST", body: form });
    return { id: String(row.input_artifact_id), name: String(row.name), mediaType: String(row.media_type), sizeBytes: number(row.size_bytes) };
  },
  async approve(approvalId: string, decision: "approved" | "rejected") {
    return request<Approval>(`/approvals/${encodeURIComponent(approvalId)}`, { method: "PUT", body: JSON.stringify({ decision }) });
  },
  async downloadArtifact(artifact: Artifact) {
    const response = await authorizedFetch(`/artifacts/${encodeURIComponent(artifact.id)}/content`, { method: "GET" });
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = artifact.name; anchor.click();
    URL.revokeObjectURL(url);
  },
  cancel(handle: RunHandle) {
    const path = handle.serverRunId
      ? `/agui/runs/${encodeURIComponent(handle.serverRunId)}/cancel`
      : `/agui/threads/${encodeURIComponent(handle.threadId)}/runs/${encodeURIComponent(handle.clientRunId)}/cancel`;
    return request(path, { method: "POST" });
  },
  async streamRun(agent: Agent, threadId: string, messages: Message[], prompt: string, files: InputArtifact[], callbacks: StreamCallbacks, signal?: AbortSignal) {
    const clientRunId = randomId();
    const content: unknown = files.length ? [
      { type: "text", text: prompt },
      ...files.map((file) => ({ type: inputType(file.mediaType), source: { type: "data", value: file.id, mimeType: file.mediaType }, metadata: { filename: file.name, inputArtifactId: file.id } })),
    ] : prompt;
    const history = messages.filter((message) => message.role === "user" || message.role === "assistant").map((message) => ({ id: message.id, role: message.role, content: message.content }));
    const body = { threadId, runId: clientRunId, state: {}, messages: [...history, { id: randomId(), role: "user", content }], tools: [], context: [], forwardedProps: agent.modelRoute ? { modelRoute: agent.modelRoute } : {} };
    const query = new URLSearchParams({ agent_name: agent.name, agent_version: agent.version });
    // Personal runs are always resolved to the authenticated caller. An owner
    // coordinate is only legal when a team-space grant accompanies it.
    if (agent.spaceId) {
      query.set("space_id", agent.spaceId);
      query.set("agent_owner_user_id", agent.ownerUserId);
    }
    let handle: RunHandle = { threadId, clientRunId };
    callbacks.onHandle?.(handle);
    callbacks.onTransportState?.("connected");
    const response = await authorizedFetch(`/agui?${query}`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "text/event-stream" }, body: JSON.stringify(body), signal });
    handle = { ...handle, serverRunId: response.headers.get("X-Harness-Run-ID") || undefined };
    callbacks.onHandle?.(handle);
    let terminal = false;
    const consume = async (source: Response) => readSse(source, (eventId, wire) => {
      if (eventId) { handle = { ...handle, lastEventId: eventId }; callbacks.onHandle?.(handle); }
      const event = fromAgui(wire);
      if (!event) return;
      if (event.kind === "run.finished" || event.kind === "run.error") terminal = true;
      callbacks.onEvent(event, eventId);
    }, signal);
    await consume(response);
    while (!terminal && handle.serverRunId && !signal?.aborted) {
      callbacks.onTransportState?.("recovering");
      await delay(450, signal);
      const replay = await authorizedFetch(`/agui/runs/${encodeURIComponent(handle.serverRunId)}/events`, { method: "GET", headers: { Accept: "text/event-stream", ...(handle.lastEventId ? { "Last-Event-ID": handle.lastEventId } : {}) }, signal });
      await consume(replay);
    }
  },
};

function toThread(row: Json): Thread {
  const pending = object(row.pending_approval);
  return {
    id: String(row.thread_id), sessionId: String(row.session_id), title: String(row.title || "新会话"), agentName: String(row.agent_name), agentVersion: String(row.agent_version),
    ownerUserId: String(row.agent_owner_user_id), spaceId: text(row.space_id), status: String(row.status || "idle") as Thread["status"], runId: text(row.run_id),
    createdAt: String(row.created_at), updatedAt: String(row.updated_at), archived: Boolean(row.archived_at), approval: row.pending_approval ? toApproval(pending) : undefined,
  };
}

function toApproval(row: Json): Approval {
  return { id: String(row.approval_id), runId: String(row.run_id), toolCallId: String(row.tool_call_id), status: String(row.status), reason: String(row.reason || "需要确认后继续"), toolName: text(row.tool_name), arguments: object(row.argument_summary), risk: text(row.risk) };
}

function historyMessage(row: Json, index: number, toolResults: Map<string, { result?: string }>): Message {
  const calls = Array.isArray(row.toolCalls) ? row.toolCalls as Json[] : [];
  const artifacts: Artifact[] = [];
  let activity;
  const toolCalls = calls.flatMap((call) => {
    const fn = object(call.function); const name = String(fn.name ?? "工具"); const args = String(fn.arguments ?? "");
    if (name === "harness_present_artifact") { try { const item = JSON.parse(args) as Json; artifacts.push({ id: String(item.artifact_id), runId: String(item.run_id), name: String(item.name), mediaType: String(item.media_type), sizeBytes: number(item.size_bytes), status: text(item.status) }); } catch { /* ignore malformed projection */ } return []; }
    if (name === "harness_run_activity") { try { activity = (JSON.parse(args) as Json).activity; } catch { /* ignore malformed projection */ } return []; }
    const id = String(call.id); return [{ id, name, arguments: args, result: toolResults.get(id)?.result, status: "complete" as const }];
  });
  const content = Array.isArray(row.content) ? (row.content as Json[]).filter((part) => part.type === "text").map((part) => String(part.text ?? "")).join("\n") : String(row.content ?? "");
  return { id: String(row.id || `history-${index}`), role: row.role === "user" || row.role === "system" ? row.role : "assistant", content, toolCalls, artifacts, activity: activity as Message["activity"] };
}

function inputType(mediaType: string) { if (mediaType.startsWith("image/")) return "image"; if (mediaType.startsWith("audio/")) return "audio"; if (mediaType.startsWith("video/")) return "video"; return "document"; }
function object(value: unknown): Json { return value && typeof value === "object" ? value as Json : {}; }
function text(value: unknown) { return typeof value === "string" ? value : undefined; }
function number(value: unknown) { return typeof value === "number" ? value : undefined; }
function parseStored<T>(key: string): T | null { try { const raw = localStorage.getItem(key); return raw ? JSON.parse(raw) as T : null; } catch { return null; } }
function delay(ms: number, signal?: AbortSignal) { return new Promise<void>((resolve, reject) => { const timer = window.setTimeout(resolve, ms); signal?.addEventListener("abort", () => { window.clearTimeout(timer); reject(signal.reason); }, { once: true }); }); }
