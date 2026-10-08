// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, test, vi } from "vitest";
import { VoiceInput } from "../src/components/voice-input";

const mocks = vi.hoisted(() => ({ finish: vi.fn(), cancel: vi.fn(), release: vi.fn(), refine: vi.fn(), refineResult: vi.fn(), disconnected: undefined as (() => void) | undefined }));
vi.mock("../src/lib/dictation-audio", () => ({ PcmRecorder: class {
  async start(_onData: unknown, onEnded: () => void) { mocks.disconnected = onEnded; }
  async stop() { mocks.release(); } release() { mocks.release(); }
} }));
vi.mock("../src/lib/dictation-client", () => ({
  dictationRequest: async (path: string, init?: RequestInit) => {
    if (path === "/capabilities") return { enabled: true, mode: "realtime", maxSessionSeconds: 120 };
    mocks.refine(JSON.parse(init!.body as string));
    return await (mocks.refineResult() ?? { text: "请不要改金额1200元。", status: "refined" });
  },
  DictationStream: class {
    constructor(private onText: (text: string) => void) {}
    async start() { this.onText("那个请不要改金额1200元"); }
    send() {} cancel() { mocks.cancel(); }
    async finish() { return mocks.finish() ?? { text: "那个请不要改金额1200元" }; }
  },
}));

afterEach(() => { vi.clearAllMocks(); vi.unstubAllGlobals(); });

async function mount() {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("isSecureContext", true);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia() {} } });
  const host = document.createElement("div"); document.body.appendChild(host);
  const root = createRoot(host); const insert = vi.fn(); const draft = vi.fn();
  await act(async () => root.render(<VoiceInput disabled={false} modelRoute="deepseek-v4-flash" onInsert={insert} onDraft={draft} onActive={() => undefined}/>));
  return { host, root, insert, draft };
}

test("stops once on rapid clicks, sends only text for refinement and fills the composer automatically", async () => {
  const { host, root, insert } = await mount();
  await act(async () => (host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).click());
  const stop = host.querySelector('[aria-label="停止录音并整理文字"]') as HTMLButtonElement;
  await act(async () => { stop.click(); stop.click(); });
  expect(mocks.finish).toHaveBeenCalledTimes(1);
  expect(mocks.refine).toHaveBeenCalledExactlyOnceWith({ draft: "那个请不要改金额1200元", model_route: "deepseek-v4-flash" });
  expect(host.textContent).not.toContain("插入输入框");
  expect(insert).toHaveBeenCalledExactlyOnceWith("请不要改金额1200元。");
  await act(async () => root.unmount()); host.remove();
});

test("cancellation releases capture and never starts refinement", async () => {
  const { host, root, insert } = await mount();
  await act(async () => (host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).click());
  await act(async () => (host.querySelector('[aria-label="取消语音输入"]') as HTMLButtonElement).click());
  expect(mocks.release).toHaveBeenCalled(); expect(mocks.cancel).toHaveBeenCalled();
  expect(mocks.finish).not.toHaveBeenCalled(); expect(mocks.refine).not.toHaveBeenCalled();
  expect(insert).not.toHaveBeenCalled();
  await act(async () => root.unmount()); host.remove();
});

test("empty recognition silently returns to idle without inserting or refining", async () => {
  mocks.finish.mockReturnValueOnce({ text: "  " });
  const { host, root, insert } = await mount();
  await act(async () => (host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).click());
  await act(async () => (host.querySelector('[aria-label="停止录音并整理文字"]') as HTMLButtonElement).click());
  expect(mocks.release).toHaveBeenCalled(); expect(mocks.cancel).toHaveBeenCalled();
  expect(mocks.refine).not.toHaveBeenCalled(); expect(insert).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')).toBeNull();
  expect((host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).disabled).toBe(false);
  await act(async () => root.unmount()); host.remove();
});

test("losing the last microphone finishes the draft and unlocks the composer", async () => {
  const { host, root, insert } = await mount();
  await act(async () => (host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).click());
  await act(async () => mocks.disconnected?.());
  expect(mocks.finish).toHaveBeenCalledTimes(1);
  expect(insert).toHaveBeenCalledExactlyOnceWith("请不要改金额1200元。");
  expect((host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).disabled).toBe(false);
  await act(async () => root.unmount()); host.remove();
});

 test("cancelling during refinement prevents a late automatic insertion", async () => {
  let resolve!: (value: {text: string; status: string}) => void;
  mocks.refineResult.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
  const { host, root, insert } = await mount();
  await act(async () => (host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).click());
  await act(async () => (host.querySelector('[aria-label="停止录音并整理文字"]') as HTMLButtonElement).click());
  await act(async () => (host.querySelector('[aria-label="取消语音输入"]') as HTMLButtonElement).click());
  await act(async () => resolve({text: "迟到的结果", status: "refined"}));
  expect(insert).not.toHaveBeenCalled();
  await act(async () => root.unmount()); host.remove();
});

 test("streams drafts before stopping or invoking text refinement", async () => {
  const { host, root, insert, draft } = await mount();
  await act(async () => (host.querySelector('[aria-label="语音输入"]') as HTMLButtonElement).click());
  expect(draft).toHaveBeenCalledWith("那个请不要改金额1200元");
  expect(insert).not.toHaveBeenCalled(); expect(mocks.refine).not.toHaveBeenCalled();
  await act(async () => (host.querySelector('[aria-label="取消语音输入"]') as HTMLButtonElement).click());
  await act(async () => root.unmount()); host.remove();
});
