import { afterEach, describe, expect, it, vi } from "vitest";
import { watchMentionCompletion } from "../src/lib/mention-completion";

afterEach(() => vi.unstubAllGlobals());
function stream(chunks: string[]) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(new ReadableStream({
    start(controller) { for (const text of chunks) controller.enqueue(new TextEncoder().encode(text)); controller.close(); },
  }))));
}
describe("existing child Run completion", () => {
  it("detects a terminal event across split SSE/CRLF frames", async () => {
    stream([': heartbeat\r', '\n\r', '\ndata: {"type":"TEXT_MESSAGE_CONTENT"}\r\n\r\n',
      'data: {"type":"RUN_', 'FINISHED"}\r', '\n\r\n']);
    expect(await watchMentionCompletion("actual-run", new AbortController().signal)).toBe(true);
    expect(fetch).toHaveBeenCalledWith("/api/agui/runs/actual-run/events", expect.objectContaining({ cache: "no-store" }));
  });
  it("also reconciles failed child runs without claiming success", async () => {
    stream(['data: {"type":"RUN_ERROR"}\n\n']);
    expect(await watchMentionCompletion("actual-run", new AbortController().signal)).toBe(true);
  });
  it("does not treat a dropped stream as a finished result", async () => {
    stream(['data: {"type":"TEXT_MESSAGE_CONTENT"}\n\n']);
    expect(await watchMentionCompletion("actual-run", new AbortController().signal)).toBe(false);
  });
});
