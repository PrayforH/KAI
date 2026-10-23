// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IntelligentUiBlock } from "../src/components/intelligent-ui/presentation-host";
import { ExpandablePreview } from "../src/components/intelligent-ui/expandable-preview";
import { HtmlView } from "../src/components/intelligent-ui/html-view";
import { parsePresentation, parsePresentationBody, type Presentation } from "../src/components/intelligent-ui/schema";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
const p: Presentation = {
  schema_version: "harness.ui/v1", presentation_id: "ui_html", revision: 1,
  run_id: "run-1", message_id: "assistant-run-1", placement: "inline",
  kind: "html", renderer_version: "html/1", fallback_text: "An interactive tool",
  data: { artifact_id: "artifact_html", name: "tool.html", sha256: "a".repeat(64), size_bytes: 100 },
  config: { height: 560 }, actions: [], decision: { origin: "model" },
  source_refs: [], snapshot_hash: "hash",
};
beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });

function render(onState = vi.fn(), ready = true) {
  act(() => root.render(<HtmlView presentation={p} height={560} ready={ready}
    initialState={{ people: 8 }} onState={onState} />));
  return onState;
}
function frame() { return host.querySelector("iframe")!; }
function token() { return new URLSearchParams(new URL(frame().src).hash.slice(1)).get("harness_ui_token"); }
function message(data: unknown, source: MessageEventSource | null = frame().contentWindow, origin = "null") {
  act(() => window.dispatchEvent(new MessageEvent("message", { data, source, origin })));
}

it("uses a presentation-scoped document and an opaque script sandbox", () => {
  render();
  expect(frame().getAttribute("sandbox")).toBe("allow-scripts");
  expect(frame().src).toContain("/api/ui/presentations/ui_html/html?revision=1#harness_ui_token=");
  expect(token()).toMatch(/^[a-f0-9]{32}$/);
  expect(frame().getAttribute("referrerpolicy")).toBe("no-referrer");
});

it("waits for the owning view state before mounting a tool", () => {
  render(vi.fn(), false);
  expect(host.querySelector("iframe")).toBeNull();
  expect(host.textContent).toContain("正在恢复工具");
});

it("restores once to the matching frame and admits only its state channel", () => {
  const changed = render();
  const reply = vi.spyOn(frame().contentWindow!, "postMessage");
  const nonce = token();
  message({ type: "harness.ui.ready", token: nonce });
  expect(reply).toHaveBeenCalledWith({ type: "harness.ui.init", token: nonce, state: { people: 8 } }, "*");
  message({ type: "harness.ui.ready", token: nonce });
  expect(reply).toHaveBeenCalledTimes(1);
  message({ type: "harness.ui.state", token: nonce, state: { people: 5 } });
  expect(changed).toHaveBeenCalledWith({ people: 5 });
  expect(host.textContent).not.toContain("此工具未启用状态恢复");
});

it("does not flash a stateless warning during handshake and clears it when the tool becomes ready", () => {
  vi.useFakeTimers();
  try {
    render();
    act(() => vi.advanceTimersByTime(2999));
    expect(host.textContent).not.toContain("此工具未启用状态恢复");
    act(() => vi.advanceTimersByTime(1));
    expect(host.textContent).toContain("此工具未启用状态恢复");
    message({ type: "harness.ui.ready", token: token() });
    expect(host.textContent).not.toContain("此工具未启用状态恢复");
  } finally { vi.useRealTimers(); }
});

it("refuses other windows, origins, tokens, extra fields and action messages", () => {
  const changed = render();
  const nonce = token();
  message({ type: "harness.ui.ready", token: nonce });
  const data = { type: "harness.ui.state", token: nonce, state: { people: 4 } };
  message(data, window);
  message(data, frame().contentWindow, "https://example.com");
  message({ ...data, token: "wrong" });
  message({ ...data, action_id: "submit" });
  message({ ...data, type: "harness.ui.action" });
  message({ ...data, state: [] });
  message({ ...data, state: { invalid: NaN } });
  message({ ...data, state: { huge: "x".repeat(12001) } });
  expect(changed).not.toHaveBeenCalled();
});

it("does not render model-authored markup or navigation in HTML data", () => {
  expect(parsePresentation(p)).not.toBeNull();
  expect(parsePresentationBody(p)?.kind).toBe("html");
  expect(parsePresentationBody({ ...p, data: { ...p.data, url: "https://example.com" } })).toBeNull();
  expect(parsePresentationBody({ ...p, data: { artifact_id: "artifact_html", html: "<script/>" } })).toBeNull();
});

it("recovers a transient missing snapshot, then mounts the published HTML", async () => {
  vi.useFakeTimers();
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(Response.json({detail:"presentation not found"}, {status:404}))
    .mockResolvedValueOnce(Response.json({...p, view_state:{version:0, state:{}}, action_requests:[]}));
  try {
    await act(async () => root.render(<IntelligentUiBlock value={p} connected />));
    expect(host.querySelector("iframe")).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(250));
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(host.querySelector("iframe")).not.toBeNull();
    expect(host.textContent).not.toContain("presentation not found");
  } finally { fetcher.mockRestore(); vi.useRealTimers(); }
});
it("ends bounded recovery with a readable error and permits an explicit retry", async () => {
  vi.useFakeTimers();
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockResolvedValue(Response.json({detail:"presentation not found"}, {status:404}));
  try {
    await act(async () => root.render(<IntelligentUiBlock value={p} connected />));
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(host.querySelector("iframe")).toBeNull();
    expect(host.textContent).not.toContain("正在恢复工具");
    expect(host.textContent).not.toContain("presentation not found");
    expect(host.textContent).toContain("交互工具暂时无法恢复");
    fetcher.mockResolvedValue(Response.json({...p, view_state:{version:0, state:{}}, action_requests:[]}));
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>("button")].find(b => b.textContent === "重新加载交互工具")!.click());
    expect(host.querySelector("iframe")).not.toBeNull();
  } finally { fetcher.mockRestore(); vi.useRealTimers(); }
});

it("automatically recovers a transport interruption without printing browser errors", async () => {
  vi.useFakeTimers();
  const fetcher = vi.spyOn(globalThis, "fetch")
    .mockRejectedValueOnce(new TypeError("Failed to fetch"))
    .mockResolvedValueOnce(Response.json({...p, view_state:{version:0, state:{}}, action_requests:[]}));
  try {
    await act(async () => root.render(<IntelligentUiBlock value={p} connected />));
    expect(host.textContent).not.toContain("Failed to fetch");
    await act(async () => vi.advanceTimersByTimeAsync(250));
    expect(host.querySelector("iframe")).not.toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(2);
  } finally { fetcher.mockRestore(); vi.useRealTimers(); }
});


it("expands and collapses the same live document, preserving its state channel and focus", () => {
  const close = vi.fn(function (this: HTMLDialogElement) { this.open = false; });
  const show = vi.fn(function (this: HTMLDialogElement) { this.open = true; });
  const modal = vi.fn(function (this: HTMLDialogElement) { this.open = true; });
  Object.assign(HTMLDialogElement.prototype, { close, show, showModal: modal });
  const changed = vi.fn();
  try {
    act(() => root.render(<ExpandablePreview title="地图">{control => <>{control}
      <HtmlView presentation={p} height={560} ready onState={changed} />
    </>}</ExpandablePreview>));
    const original = frame();
    const nonce = token();
    message({ type: "harness.ui.ready", token: nonce });
    const button = host.querySelector<HTMLButtonElement>("button")!;
    act(() => button.click());
    expect(modal).toHaveBeenCalledOnce();
    expect(host.querySelector("dialog")?.getAttribute("aria-modal")).toBe("true");
    expect(frame()).toBe(original);
    expect(token()).toBe(nonce);
    message({ type: "harness.ui.state", token: nonce, state: { selected: 2 } });
    expect(changed).toHaveBeenCalledWith({ selected: 2 });
    act(() => host.querySelector("dialog")!.dispatchEvent(new Event("cancel", { cancelable: true })));
    expect(show).toHaveBeenCalledOnce();
    expect(frame()).toBe(original);
    expect(frame().getAttribute("sandbox")).toBe("allow-scripts");
    expect(document.activeElement).toBe(button);
    expect(button.getAttribute("aria-expanded")).toBe("false");
  } finally {
    Reflect.deleteProperty(HTMLDialogElement.prototype, "close");
    Reflect.deleteProperty(HTMLDialogElement.prototype, "show");
    Reflect.deleteProperty(HTMLDialogElement.prototype, "showModal");
  }
});
