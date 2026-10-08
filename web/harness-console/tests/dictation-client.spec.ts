import { afterEach, expect, test, vi } from "vitest";
import { DictationStream } from "../src/lib/dictation-client";
import { pcm16 } from "../src/lib/dictation-audio";

afterEach(() => vi.unstubAllGlobals());

test("PCM wire data is little endian, bounded and headerless", () => {
  const data = new DataView(pcm16(new Float32Array([-2, 0, 2])));
  expect(data.byteLength).toBe(6);
  expect(data.getInt16(0, true)).toBe(-32768);
  expect(data.getInt16(4, true)).toBe(32767);
});

test("uploads frames in sequence and waits for SSE tail before returning", async () => {
  const sequence: number[] = []; const previews: string[] = [];
  let sse!: ReadableStreamDefaultController<Uint8Array>;
  const encode = (data: object) => new TextEncoder().encode(`data: ${JSON.stringify(data)}\n\n`);
  const errors = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/sessions")) return Response.json({ id: "session-a" }, { status: 201 });
    if (url.endsWith("/events")) return new Response(new ReadableStream({ start(controller) { sse = controller; } }));
    if (url.includes("/audio?")) {
      sequence.push(Number(new URL(url, "http://test").searchParams.get("sequence")));
      expect((init?.body as ArrayBuffer).byteLength).toBe(6);
      sse.enqueue(encode({ type: "draft", text: "请不要改金额1200" }));
      return Response.json({});
    }
    if (url.endsWith("/finish")) {
      sse.enqueue(encode({ type: "done", text: "请不要改金额1200元。" })); sse.close();
      return Response.json({ text: "请不要改金额1200元。" });
    }
    return new Response(null, { status: 204 });
  }));
  const stream = new DictationStream((text) => previews.push(text), errors);
  await stream.start(); stream.send(new Float32Array(3)); stream.send(new Float32Array(3));
  expect(await stream.finish()).toEqual({ text: "请不要改金额1200元。" });
  expect(sequence).toEqual([0, 1]);
  expect(previews.at(-1)).toBe("请不要改金额1200元。");
  expect(errors).not.toHaveBeenCalled(); stream.cancel();
});
