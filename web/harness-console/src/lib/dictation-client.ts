import { requireAuthenticatedResponse } from "./client-auth";
import { pcm16 } from "./dictation-audio";

const ROOT = "/api/harness/dictation";

export async function dictationRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = requireAuthenticatedResponse(await fetch(`${ROOT}${path}`, init));
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.error?.message || body.detail || "语音服务暂时不可用");
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}

export class DictationStream {
  private controller = new AbortController();
  private id = "";
  private queue = Promise.resolve();
  private sequence = 0;
  private queuedBytes = 0;
  private failure?: Error;
  private closed = false;
  private ending = false;
  private events?: Promise<void>;

  constructor(private onText: (text: string) => void, private onError: (error: Error) => void) {}

  private fail(error: unknown) {
    if (this.closed || this.failure) return;
    this.failure = error instanceof Error ? error : new Error("实时语音连接已中断");
    this.onError(this.failure);
  }

  async start() {
    const result = await dictationRequest<{ id: string }>("/sessions", {
      method: "POST", signal: this.controller.signal,
    });
    this.id = result.id;
    if (this.closed) { this.cancel(); return; }
    const response = requireAuthenticatedResponse(await fetch(`${ROOT}/sessions/${this.id}/events`, {
      signal: this.controller.signal,
    }));
    if (!response.ok || !response.body) throw new Error("无法接收实时语音结果");
    this.events = this.read(response.body).catch((error: unknown) => this.fail(error));
  }

  private async read(body: ReadableStream<Uint8Array>) {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let completed = false;
    try {
      while (!this.closed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let boundary: number;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const event = buffer.slice(0, boundary); buffer = buffer.slice(boundary + 2);
          const raw = event.split("\n").filter((line) => line.startsWith("data: ")).map((line) => line.slice(6)).join("\n");
          if (!raw) continue;
          const item = JSON.parse(raw) as { type: string; text?: string; message?: string };
          if (item.type === "error") throw new Error(item.message || "语音识别失败");
          if ((item.type === "draft" || item.type === "done") && typeof item.text === "string") this.onText(item.text);
          if (item.type === "done") { completed = true; return; }
        }
        if (buffer.length > 128000) throw new Error("语音结果格式异常");
      }
      if (!this.closed && !completed) throw new Error("实时语音连接已中断，可保留已显示的草稿");
    } finally { reader.releaseLock(); }
  }

  send(samples: Float32Array) {
    if (this.closed || this.ending || this.failure || !samples.length) return;
    const data = pcm16(samples);
    if (this.queuedBytes + data.byteLength > 160000) {
      this.fail(new Error("语音服务响应较慢，录音已停止。可保留已显示的草稿。")); return;
    }
    const sequence = this.sequence++;
    this.queuedBytes += data.byteLength;
    this.queue = this.queue.then(async () => {
      if (this.closed || this.failure) return;
      await dictationRequest(`/sessions/${this.id}/audio?sequence=${sequence}`, {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: data,
        signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(10000)]),
      });
    }).catch((error: unknown) => this.fail(error)).finally(() => { this.queuedBytes -= data.byteLength; });
  }

  async finish() {
    this.ending = true;
    await this.queue;
    if (this.failure) throw this.failure;
    const result = await dictationRequest<{ text: string }>(`/sessions/${this.id}/finish`, {
      method: "POST", signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(25000)]),
    });
    await this.events;
    if (this.failure) throw this.failure;
    return result;
  }

  cancel() {
    this.closed = true; this.controller.abort();
    if (this.id) {
      void fetch(`${ROOT}/sessions/${this.id}`, { method: "DELETE", keepalive: true }).catch(() => undefined);
      this.id = "";
    }
  }
}
