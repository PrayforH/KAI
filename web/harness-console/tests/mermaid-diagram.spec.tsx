// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MermaidDiagram } from "../src/components/mermaid-diagram";

const engine = vi.hoisted(() => ({ initialize: vi.fn(), render: vi.fn() }));
vi.mock("mermaid", () => ({ default: engine }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const source = "flowchart LR\n A[提交任务] --> B[排队]";
let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.useFakeTimers();
  engine.initialize.mockReset();
  engine.render
    .mockReset()
    .mockResolvedValue({
      svg: "<svg><text>提交任务</text><text>排队</text></svg>",
    });
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: undefined,
  });
  Object.defineProperty(document, "execCommand", {
    configurable: true,
    value: vi.fn(() => false),
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root.render(<MermaidDiagram code={source} />));
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
});

function copyButton() {
  return [...host.querySelectorAll("button")].find(
    (button) => button.getAttribute("aria-label") === "复制图表源码",
  )!;
}

it("reports a failed clipboard operation without claiming the source was copied", async () => {
  await act(async () => copyButton().click());
  expect(host.textContent).not.toContain("已复制");
  expect(host.querySelector('[role="alert"]')?.textContent).toContain(
    "复制失败",
  );
});

it("copies source on HTTP deployments through the selection fallback", async () => {
  vi.mocked(document.execCommand).mockReturnValue(true);
  await act(async () => copyButton().click());
  expect(host.textContent).toContain("已复制");
  expect(document.execCommand).toHaveBeenCalledWith("copy");
  expect(document.querySelector("textarea")).toBeNull();
});

it("renders diagrams with strict Mermaid security and text labels", async () => {
  await act(async () => vi.advanceTimersByTimeAsync(420));
  expect(engine.initialize).toHaveBeenCalledWith(
    expect.objectContaining({
      securityLevel: "strict",
      flowchart: { htmlLabels: false, useMaxWidth: true },
    }),
  );
  expect(engine.render).toHaveBeenCalledWith(expect.any(String), source);
  expect(host.querySelector('[role="img"]')?.textContent).toBe("提交任务排队");
});

it("keeps readable source when Mermaid rejects the diagram", async () => {
  engine.render.mockRejectedValue(new Error("Invalid syntax"));
  await act(async () => vi.advanceTimersByTimeAsync(420));
  expect(host.querySelector("pre code")?.textContent).toBe(source);
  expect([...host.querySelectorAll('[role="status"]')].map(node => node.textContent).join(" ")).toContain(
    "图表语法暂时无法渲染",
  );
});
