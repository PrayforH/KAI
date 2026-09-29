// @vitest-environment jsdom
import { act, forwardRef } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { SentUserContent } from "../src/components/sent-user-content";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
vi.mock("@assistant-ui/react-ui", () => ({ UserMessage: {
  Content: forwardRef<HTMLDivElement, Record<string, unknown>>(function Content(props, ref) {
    return <div {...props} ref={ref} style={{ fontSize: 16 }}>完整原文保留在消息中</div>;
  }),
} }));
it("folds only a tall submitted message and keeps its full text on expand and collapse", () => {
  const height = vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(600);
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  try {
    act(() => root.render(<SentUserContent />));
    const content = host.querySelector(".sent-user-content")!;
    const button = host.querySelector("button")!;
    expect(content.getAttribute("data-collapsed")).toBe("true");
    expect(button.textContent).toBe("展开全文");
    act(() => button.click()); expect(content.getAttribute("data-collapsed")).toBe("false");
    expect(button.textContent).toBe("收起全文");
    act(() => button.click()); expect(content.getAttribute("data-collapsed")).toBe("true");
    expect(content.textContent).toBe("完整原文保留在消息中");
  } finally { act(() => root.unmount()); host.remove(); height.mockRestore(); vi.unstubAllGlobals(); }
});
