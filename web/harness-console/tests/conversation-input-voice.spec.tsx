// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationInputCard } from "../src/components/conversation-input-card";

const mocks = vi.hoisted(() => ({ transcribe: vi.fn() }));
vi.mock("../src/lib/dictation-audio", async original => ({
  ...await original<typeof import("../src/lib/dictation-audio")>(), PcmRecorder: class {
    async start(onData: (s: Float32Array) => void) { onData(new Float32Array(16000)); }
    async stop() {} release() {}
  },
}));
vi.mock("../src/lib/dictation-client", () => ({
  dictationRequest: async (path: string) => {
    if (path === "/capabilities") return { enabled: true, maxSessionSeconds: 120 };
    mocks.transcribe(path); return { text: "语音回答" };
  },
}));
let root: Root; let host: HTMLDivElement;
afterEach(async () => { await act(async () => root?.unmount()); host?.remove(); vi.clearAllMocks(); vi.unstubAllGlobals(); });
async function mount() {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true); vi.stubGlobal("isSecureContext", true);
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: { getUserMedia() {} } });
  host = document.createElement("div"); host.className = "aui-composer-root"; document.body.append(host); root = createRoot(host);
  const submit = vi.fn(); const active = vi.fn();
  await act(async () => root.render(<><div className="composer-footer" /><ConversationInputCard disabled={false}
    modelRoute="selected-model" onVoiceActive={active} onSubmit={submit} input={{version: 1, title: "补充信息", questions: [
      {id: "scope", label: "范围", type: "text", options: []}, {id: "time", label: "时间", type: "text", options: []},
    ]}} /></>));
  return {submit, active};
}
function button(label: string) { return [...host.querySelectorAll<HTMLButtonElement>("button")].find(el => el.getAttribute("aria-label") === label || el.textContent === label)!; }
function type(text: string) {
  const input = host.querySelector<HTMLInputElement>('input[type="text"]')!;
  act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, text); input.dispatchEvent(new Event("input", {bubbles: true})); });
  return input;
}
it("inserts at the current selection, keeps controls in the card and waits for explicit submission", async () => {
  const {submit, active} = await mount(); const input = type("前旧内容后"); input.setSelectionRange(1, 4);
  await act(async () => button("语音输入").click());
  expect(input.value).toBe("前旧内容后"); expect(active).toHaveBeenLastCalledWith(true);
  expect(host.querySelector(".composer-footer")?.children).toHaveLength(0);
  expect(button("下一题").disabled).toBe(true); expect(button("跳过").disabled).toBe(true);
  await act(async () => button("完成录音并转写").click());
  expect(input.value).toBe("前语音回答后"); expect(active).toHaveBeenLastCalledWith(false);
  expect(submit).not.toHaveBeenCalled(); expect(mocks.transcribe).toHaveBeenCalledExactlyOnceWith("/transcribe");
  await act(async () => button("跳过").click());
  expect(submit).toHaveBeenCalledWith(expect.stringContaining("前语音回答后"));
});
it("cancels recording without changing the draft and keeps the answer on the correct question", async () => {
  const {submit} = await mount(); const input = type("原回答"); input.setSelectionRange(3, 3);
  await act(async () => button("语音输入").click());
  await act(async () => button("取消语音输入").click());
  expect(input.value).toBe("原回答"); expect(submit).not.toHaveBeenCalled();
  act(() => button("下一题").click()); expect(input.value).toBe("");
  act(() => button("上一题").click()); expect(input.value).toBe("原回答");
});
