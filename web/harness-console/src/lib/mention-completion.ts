import { requireAuthenticatedResponse } from "./client-auth";

/** Observe the existing child Run stream. No polling, new Run, or replayed work. */
export async function watchMentionCompletion(runId: string, signal: AbortSignal): Promise<boolean> {
  const response = requireAuthenticatedResponse(await fetch(
    `/api/agui/runs/${encodeURIComponent(runId)}/events`,
    { signal, cache: "no-store", headers: { Accept: "text/event-stream" } },
  ));
  if (!response.ok || !response.body) return false;
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) return false;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let end: number;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = frame.split("\n").filter(line => line.startsWith("data:"))
          .map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        try {
          const event = JSON.parse(data) as { type?: string };
          if (event.type === "RUN_FINISHED" || event.type === "RUN_ERROR") return true;
        } catch { /* SSE heartbeats and unrelated frames are not completion. */ }
      }
    }
    return false;
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
