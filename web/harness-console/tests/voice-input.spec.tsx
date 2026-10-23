// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { VoiceInput } from "../src/components/voice-input";
const mocks = vi.hoisted(() => ({ start: vi.fn(), stop: vi.fn(), release: vi.fn(), request: vi.fn(),
  data: undefined as ((s: Float32Array) => void) | undefined, ended: undefined as (() => void) | undefined }));
vi.mock("../src/lib/dictation-audio", async original => ({
  ...await original<typeof import("../src/lib/dictation-audio")>(), PcmRecorder: class {
    async start(data: (s: Float32Array) => void, ended: () => void) { mocks.data = data; mocks.ended = ended; await mocks.start(); }
    async stop() { await mocks.stop(); mocks.release(); } release() { mocks.release(); }
  },
}));
vi.mock("../src/lib/dictation-client", () => ({ dictationRequest: (p: string, i?: RequestInit) => mocks.request(p, i) }));
let root: Root | undefined; let host: HTMLDivElement;
beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true); vi.stubGlobal("isSecureContext", true);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia() {} } });
  mocks.request.mockImplementation(async p => p === "/capabilities" ? { enabled: true, maxSessionSeconds: 120 } : { text: "请不要修改金额1200元。" });
});
afterEach(async () => { await act(async () => root?.unmount()); root = undefined; host?.remove(); vi.resetAllMocks(); vi.unstubAllGlobals(); });
async function mount() {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  const insert = vi.fn(), draft = vi.fn(), active = vi.fn();
  await act(async () => root!.render(<VoiceInput disabled={false} modelRoute="selected" onInsert={insert} onDraft={draft} onActive={active}/>));
  return { insert, draft, active };
}
function button(label: string) { return [...host.querySelectorAll<HTMLButtonElement>("button")].find(el => el.getAttribute("aria-label") === label || el.textContent === label)!; }
async function record() { await act(async () => button("语音输入").click()); act(() => mocks.data?.(new Float32Array(16000).fill(.2))); }
async function finish() { await act(async () => button("完成录音并转写").click()); }
function uploads() { return mocks.request.mock.calls.filter(([p]) => p !== "/capabilities"); }
test("records locally then stops the mic and uploads one complete WAV without streaming or refinement", async () => {
  const { insert, draft, active } = await mount(); await record();
  expect(uploads()).toHaveLength(0); expect(insert).not.toHaveBeenCalled(); expect(draft).not.toHaveBeenCalled();
  mocks.stop.mockImplementationOnce(() => mocks.data?.(new Float32Array(4000)));
  mocks.request.mockImplementationOnce(async (p, i) => {
    expect(p).toBe("/transcribe"); expect(mocks.release).toHaveBeenCalled();
    expect((i.body as FormData).get("file")).toMatchObject({ size: 40044, type: "audio/wav" });
    return { text: "请不要修改金额1200元。" };
  });
  const stop = button("完成录音并转写"); await act(async () => { stop.click(); stop.click(); });
  expect(mocks.stop).toHaveBeenCalledOnce(); expect(uploads()).toHaveLength(1);
  expect(insert).toHaveBeenCalledExactlyOnceWith("请不要修改金额1200元。"); expect(active).toHaveBeenLastCalledWith(false);
});
test("cancel releases capture without uploading or modifying the draft", async () => {
  const { insert, draft, active } = await mount(); await record(); await act(async () => button("取消语音输入").click());
  expect(mocks.release).toHaveBeenCalled(); expect(uploads()).toHaveLength(0);
  expect(insert).not.toHaveBeenCalled(); expect(draft).not.toHaveBeenCalled(); expect(active).toHaveBeenLastCalledWith(false);
});
test("transcribing displays progress, cancel aborts and ignores late results", async () => {
  const { insert } = await mount(); await record(); let resolve!: (v: {text: string}) => void;
  mocks.request.mockReturnValueOnce(new Promise(done => { resolve = done; })); await finish();
  expect(host.textContent).toContain("正在转写"); expect(insert).not.toHaveBeenCalled();
  const signal = uploads()[0][1].signal as AbortSignal;
  await act(async () => button("取消语音输入").click()); expect(signal.aborted).toBe(true);
  await act(async () => resolve({ text: "迟到的文字" })); expect(insert).not.toHaveBeenCalled();
});
test("failure keeps the recording for explicit retry without reopening capture", async () => {
  const { insert } = await mount(); await record(); mocks.request.mockRejectedValueOnce(new Error("语音服务暂时不可用"));
  await finish(); expect(host.querySelector('[role="alert"]')?.textContent).toContain("暂时不可用"); expect(insert).not.toHaveBeenCalled();
  const original = (uploads()[0][1].body as FormData).get("file") as File;
  await act(async () => button("重试转写").click());
  expect(((uploads()[1][1].body as FormData).get("file") as File).size).toBe(original.size);
  expect(mocks.start).toHaveBeenCalledOnce(); expect(insert).toHaveBeenCalledOnce();
});
test("silence and short recordings offer rerecording", async () => {
  const { insert } = await mount(); await record(); mocks.request.mockResolvedValueOnce({ text: " " }); await finish();
  expect(host.textContent).toContain("没有识别到语音"); await act(async () => button("重新录音").click()); await finish();
  expect(host.textContent).toContain("录音太短"); expect(uploads()).toHaveLength(1); expect(insert).not.toHaveBeenCalled();
});
test("device disconnect finalizes only once", async () => {
  const { insert } = await mount(); await record(); await act(async () => { mocks.ended?.(); mocks.ended?.(); });
  expect(mocks.stop).toHaveBeenCalledOnce(); expect(uploads()).toHaveLength(1); expect(insert).toHaveBeenCalledOnce();
});
test("cancel during permission prompt releases late capture", async () => {
  const { insert } = await mount(); let resolve!: () => void;
  mocks.start.mockReturnValueOnce(new Promise<void>(done => { resolve = done; }));
  await act(async () => button("语音输入").click()); await act(async () => button("取消语音输入").click());
  await act(async () => { resolve(); mocks.data?.(new Float32Array(16000)); });
  expect(mocks.release).toHaveBeenCalled(); expect(uploads()).toHaveLength(0); expect(insert).not.toHaveBeenCalled();
});
test("permission errors remain visible and dismissible", async () => {
  await mount(); mocks.start.mockRejectedValueOnce(new DOMException("denied", "NotAllowedError"));
  await act(async () => button("语音输入").click()); expect(host.textContent).toContain("请允许使用麦克风");
  await act(async () => button("取消语音输入").click()); expect(button("语音输入").disabled).toBe(false);
});
test("duration limit caps audio and automatically finalizes", async () => {
  const { insert } = await mount(); await record(); await act(async () => mocks.data?.(new Float32Array(121 * 16000)));
  expect((uploads()[0][1].body as FormData).get("file")).toMatchObject({ size: 120 * 32000 + 44 }); expect(insert).toHaveBeenCalledOnce();
});
test("unmount aborts transcription and ignores stale results", async () => {
  const { insert } = await mount(); await record(); let resolve!: (v: {text: string}) => void;
  mocks.request.mockReturnValueOnce(new Promise(done => { resolve = done; })); await finish();
  const signal = uploads()[0][1].signal as AbortSignal;
  await act(async () => root!.unmount()); root = undefined; expect(signal.aborted).toBe(true);
  await act(async () => resolve({ text: "迟到文字" })); expect(insert).not.toHaveBeenCalled();
});
