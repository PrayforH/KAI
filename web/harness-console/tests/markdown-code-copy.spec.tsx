// @vitest-environment jsdom
import { TextMessagePartProvider } from "@assistant-ui/react";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { MarkdownText } from "../src/components/markdown-text";
import { writeTextToClipboard } from "../src/lib/clipboard";
vi.mock("../src/lib/clipboard", () => ({ writeTextToClipboard: vi.fn() }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined; let host: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; host?.remove(); vi.clearAllMocks(); });
async function mount() {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root!.render(<TextMessagePartProvider text={'```js\n  const value = 1;\n```'} isRunning={false}><MarkdownText /></TextMessagePartProvider>));
  return host.querySelector<HTMLButtonElement>('[aria-label="复制代码"]')!;
}
it("copies original code and reports confirmed success", async () => {
  vi.mocked(writeTextToClipboard).mockResolvedValue(true); const button = await mount(); expect(button).toBeTruthy();
  await act(async () => button.click()); expect(writeTextToClipboard).toHaveBeenCalledTimes(1);
  expect(vi.mocked(writeTextToClipboard).mock.calls[0][0]).toContain("  const value = 1;");
  expect(button.dataset.copyState).toBe("copied"); expect(button.textContent).toContain("已复制");
});
it("reports a denied clipboard write rather than claiming success", async () => {
  vi.mocked(writeTextToClipboard).mockResolvedValue(false); const button = await mount(); await act(async () => button.click());
  expect(button.dataset.copyState).toBe("failed"); expect(button.textContent).toContain("复制失败"); expect(button.textContent).not.toContain("已复制");
});
it("prevents concurrent copies and handles unmount during a pending copy", async () => {
  let finish!: (ok: boolean) => void;
  vi.mocked(writeTextToClipboard).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const button = await mount(); await act(async () => { button.click(); button.click(); });
  expect(writeTextToClipboard).toHaveBeenCalledTimes(1); expect(button.disabled).toBe(true);
  await act(async () => root!.unmount()); root = undefined; await act(async () => finish(true));
});
