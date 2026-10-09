// @vitest-environment jsdom
import { act, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, test, vi } from "vitest";
import { useDictationComposer } from "../src/components/use-dictation-composer";

let callbacks!: ReturnType<typeof useDictationComposer>;
let composing!: {current: boolean};
let setText!: (text: string) => void;
const cleanups: (() => Promise<void>)[] = [];
afterEach(async () => { for (const cleanup of cleanups.splice(0)) await cleanup(); vi.unstubAllGlobals(); });
async function mount(initial = "前文后文") {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("requestAnimationFrame", (fn: () => void) => { queueMicrotask(fn); return 0; });
  function Harness() {
    const [text, update] = useState(initial);
    const buffer = useRef(initial), input = useRef<HTMLTextAreaElement>(null);
    composing = useRef(false);
    setText = (value) => { buffer.current = value; update(value); };
    callbacks = useDictationComposer(input, () => buffer.current, setText, composing);
    return <textarea ref={input} value={text} onChange={(event) => setText(event.target.value)} />;
  }
  const host = document.createElement("div"); document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<Harness/>));
  cleanups.push(async () => { await act(async () => root.unmount()); host.remove(); });
  const input = host.querySelector("textarea")!;
  input.setSelectionRange(2, 2);
  return input;
}

test("drafts appear in the original textarea before stop, with refinement replacing the same span", async () => {
  const input = await mount();
  await act(async () => { callbacks.onActive(true); callbacks.onDraft("实"); callbacks.onDraft("实时文字"); });
  expect(input.value).toBe("前文实时文字后文");
  expect(input.selectionStart).toBe(6);
  await act(async () => { callbacks.onInsert("整理文字。"); callbacks.onActive(false); });
  expect(input.value).toBe("前文整理文字。后文");
});

test("reading earlier dictated text keeps both its selection and scroll position across drafts and refinement", async () => {
  const input = await mount();
  await act(async () => { callbacks.onActive(true); callbacks.onDraft("上海的数据，武汉的数据"); });
  Object.defineProperty(input, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(input, "clientHeight", { configurable: true, value: 200 });
  input.scrollTop = 320;
  input.setSelectionRange(3, 7, "backward");
  await act(async () => callbacks.onDraft("上海的数据，武汉的数据，还有北京的数据"));
  expect([input.selectionStart, input.selectionEnd, input.selectionDirection]).toEqual([3, 7, "backward"]);
  expect(input.scrollTop).toBe(320);
  await act(async () => { callbacks.onInsert("上海的数据，武汉的数据，还有北京的数据。"); callbacks.onActive(false); });
  expect([input.selectionStart, input.selectionEnd]).toEqual([3, 7]);
  expect(input.scrollTop).toBe(320);
});

test("drafts follow the bottom until the reader scrolls up, then resume at the bottom", async () => {
  const input = await mount();
  await act(async () => { callbacks.onActive(true); callbacks.onDraft("实时文字"); });
  Object.defineProperty(input, "scrollHeight", { configurable: true, get: () => height });
  Object.defineProperty(input, "clientHeight", { configurable: true, value: 200 });
  let height = 1000;
  input.scrollTop = 800;
  await act(async () => { callbacks.onDraft("实时文字继续"); height = 1100; });
  expect(input.scrollTop).toBe(1100);
  input.scrollTop = 300;
  await act(async () => callbacks.onDraft("实时文字继续增加"));
  expect(input.scrollTop).toBe(300);
  input.scrollTop = 900;
  await act(async () => callbacks.onDraft("实时文字继续增加尾部"));
  expect(input.scrollTop).toBe(1100);
});

test("cancel rolls back just the current recording and preserves manual edits outside it", async () => {
  const input = await mount();
  await act(async () => { callbacks.onActive(true); callbacks.onDraft("草稿"); });
  await act(async () => { setText("新前文草稿后文"); callbacks.onChange("新前文草稿后文"); });
  await act(async () => { setText("新前文草稿后文手写"); callbacks.onChange("新前文草稿后文手写"); });
  await act(async () => callbacks.onActive(false));
  expect(input.value).toBe("新前文后文手写");
});

test("manual edits to speech are never overwritten or reinserted", async () => {
  const input = await mount();
  await act(async () => { callbacks.onActive(true); callbacks.onDraft("识别文字"); });
  await act(async () => setText("前文人工修改后文"));
  await act(async () => { callbacks.onDraft("更多识别"); callbacks.onInsert("整理结果"); callbacks.onActive(false); });
  expect(input.value).toBe("前文人工修改后文");
});

test("IME composition is not interrupted by partials or refinement", async () => {
  const input = await mount();
  await act(async () => { callbacks.onActive(true); callbacks.onDraft("草稿"); });
  composing.current = true;
  await act(async () => { callbacks.onDraft("更长草稿"); callbacks.onInsert("精修文字"); callbacks.onActive(false); });
  expect(input.value).toBe("前文草稿后文");
});
