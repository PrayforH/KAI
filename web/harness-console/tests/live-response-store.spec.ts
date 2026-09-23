import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { liveResponseStore } from "../src/lib/live-response-store";

describe("liveResponseStore", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    liveResponseStore.clear();
  });
  afterEach(() => {
    liveResponseStore.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps a short progress preface out of the response when a tool follows", () => {
    liveResponseStore.startRun("run-1");
    liveResponseStore.startMessage("assistant-run-1");
    liveResponseStore.startMessage("commentary");
    liveResponseStore.append("commentary", "先检索资料");

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: "先检索资料",
      status: "streaming",
      visible: false,
    });

    liveResponseStore.hideForTool();
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: "先检索资料",
      visible: false,
    });

    liveResponseStore.startMessage("final");
    liveResponseStore.append("final", "最终");
    liveResponseStore.append("final", "回答");
    liveResponseStore.completeMessage("final");

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: "最终回答",
      status: "streaming",
      visible: false,
    });

    liveResponseStore.completeRun();
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      runId: "run-1",
      messageId: "assistant-run-1",
      text: "最终回答",
      status: "complete",
      visible: true,
    });
  });

  it("keeps the durable turn owner when provider text parts change around tools", () => {
    liveResponseStore.startRun("run-boundaries");
    liveResponseStore.startMessage("assistant-run-boundaries");
    liveResponseStore.startMessage("provider-progress");
    liveResponseStore.append("provider-progress", "继续核验。 ");
    liveResponseStore.hideForTool();
    liveResponseStore.completeMessage("provider-progress");

    liveResponseStore.startMessage("provider-final");
    liveResponseStore.append("provider-final", "## 核验结果\n\n完整回答");
    liveResponseStore.completeMessage("provider-final");
    liveResponseStore.completeMessage("assistant-run-boundaries");
    liveResponseStore.completeRun();

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      runId: "run-boundaries",
      messageId: "assistant-run-boundaries",
      text: "## 核验结果\n\n完整回答",
      status: "complete",
      visible: true,
    });
  });

  it("reuses one durable message while replacing pre-tool progress with the final answer", () => {
    liveResponseStore.startRun("run-stable");
    liveResponseStore.startMessage("assistant-run-stable");
    liveResponseStore.append("assistant-run-stable", "先检索并核实资料。");
    liveResponseStore.hideForTool();

    liveResponseStore.append("assistant-run-stable", "## 最终结论\n\n完整回答");
    liveResponseStore.completeMessage("assistant-run-stable");
    liveResponseStore.completeRun();

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      runId: "run-stable",
      messageId: "assistant-run-stable",
      text: "## 最终结论\n\n完整回答",
      status: "complete",
      visible: true,
    });
  });

  it("does not publish a pending tool preface as visible during hand-off", () => {
    let paint: FrameRequestCallback | undefined;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      paint = callback;
      return 9;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());
    const visibleSnapshots: string[] = [];

    liveResponseStore.startRun("run-preface");
    liveResponseStore.startMessage("message-preface");
    const unsubscribe = liveResponseStore.subscribe(() => {
      const current = liveResponseStore.getSnapshot();
      if (current.visible && current.text) visibleSnapshots.push(current.text);
    });
    liveResponseStore.append("message-preface", "让我换一种方式定位章节。");
    liveResponseStore.hideForTool();
    paint?.(16);

    expect(visibleSnapshots).toEqual([]);
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: "让我换一种方式定位章节。",
      visible: false,
    });
    unsubscribe();
  });

  it("keeps a stable empty message eligible for the final response after tools", () => {
    liveResponseStore.startRun("run-terminal-response");
    liveResponseStore.startMessage("stable-message");
    liveResponseStore.hideForTool();
    liveResponseStore.append("stable-message", "最终查询结论");
    liveResponseStore.completeMessage("stable-message");
    liveResponseStore.completeRun();

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      runId: "run-terminal-response",
      messageId: "stable-message",
      text: "最终查询结论",
      status: "complete",
      visible: true,
    });
  });

  it("keeps a completed provider response visible when post-processing fails", () => {
    liveResponseStore.startRun("run-post-processing-error");
    liveResponseStore.startMessage("stable-message");
    liveResponseStore.append("stable-message", "图谱已经生成。可下载查看。");
    liveResponseStore.completeMessage("stable-message");
    liveResponseStore.failRun();

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      runId: "run-post-processing-error",
      messageId: "stable-message",
      text: "图谱已经生成。可下载查看。",
      status: "error",
      visible: true,
    });
  });

  it("publishes a stable candidate on each animation frame", () => {
    let paint: FrameRequestCallback | undefined;
    const requestFrame = vi.fn((callback: FrameRequestCallback) => {
      paint = callback;
      return 7;
    });
    const cancelFrame = vi.fn();
    vi.stubGlobal("requestAnimationFrame", requestFrame);
    vi.stubGlobal("cancelAnimationFrame", cancelFrame);

    liveResponseStore.startRun("run-smooth");
    liveResponseStore.startMessage("message-smooth");
    liveResponseStore.append("message-smooth", "平".repeat(80));
    liveResponseStore.append("message-smooth", "滑".repeat(80));

    expect(requestFrame).toHaveBeenCalledTimes(1);
    expect(liveResponseStore.getSnapshot().text).toBe("");

    paint?.(16);
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: `${"平".repeat(80)}${"滑".repeat(80)}`,
      status: "streaming",
      visible: true,
    });

    liveResponseStore.append("message-smooth", "完成");
    liveResponseStore.completeMessage("message-smooth");
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: `${"平".repeat(80)}${"滑".repeat(80)}完成`,
      status: "streaming",
      visible: true,
    });

    const notification = vi.fn();
    const unsubscribe = liveResponseStore.subscribe(notification);
    liveResponseStore.completeRun();
    expect(notification).toHaveBeenCalledOnce();
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: `${"平".repeat(80)}${"滑".repeat(80)}完成`,
      status: "complete",
      visible: true,
    });
    unsubscribe();
  });

  it("flushes pending text when animation frames are throttled", () => {
    vi.stubGlobal("requestAnimationFrame", vi.fn(() => 21));
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    liveResponseStore.startRun("run-throttled");
    liveResponseStore.startMessage("message-throttled");
    const answer = "无需刷新即可看到".repeat(24);
    liveResponseStore.append("message-throttled", answer);

    expect(liveResponseStore.getSnapshot().text).toBe("");
    vi.advanceTimersByTime(120);
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: answer,
      status: "streaming",
      visible: true,
    });
  });

  it("streams a substantial active message before the run finishes", () => {
    liveResponseStore.startRun("run-long-answer");
    liveResponseStore.startMessage("answer");
    liveResponseStore.append("answer", "正文".repeat(120));

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      messageId: "answer",
      visible: true,
      status: "streaming",
    });
  });
});

describe("response visibility deadline", () => {
  beforeEach(() => { vi.useFakeTimers(); liveResponseStore.clear(); });
  afterEach(() => { liveResponseStore.clear(); vi.useRealTimers(); });

  it("reveals a short ongoing answer without waiting for 160 characters or completion", () => {
    liveResponseStore.startRun("slow");
    liveResponseStore.startMessage("answer");
    liveResponseStore.append("answer", "这是");
    vi.advanceTimersByTime(100);
    liveResponseStore.append("answer", "回答");
    vi.advanceTimersByTime(79);
    expect(liveResponseStore.getSnapshot().visible).toBe(false);
    vi.advanceTimersByTime(1);
    expect(liveResponseStore.getSnapshot()).toMatchObject({ text: "这是回答", visible: true, status: "streaming" });
  });

  it("never reveals a preface after a tool starts or a run is replaced", () => {
    liveResponseStore.startRun("first");
    liveResponseStore.startMessage("preface");
    liveResponseStore.append("preface", "先查一下");
    liveResponseStore.hideForTool();
    vi.advanceTimersByTime(500);
    expect(liveResponseStore.getSnapshot().visible).toBe(false);
    liveResponseStore.append("preface", "旧回复");
    vi.advanceTimersByTime(100);
    liveResponseStore.startRun("second");
    liveResponseStore.startMessage("new");
    liveResponseStore.append("new", "新回复");
    vi.advanceTimersByTime(80);
    expect(liveResponseStore.getSnapshot().visible).toBe(false);
    vi.advanceTimersByTime(100);
    expect(liveResponseStore.getSnapshot()).toMatchObject({ runId: "second", text: "新回复", visible: true });
  });
});

it("does not release text ownership between a PDF progress block and subsequent tools", () => {
  liveResponseStore.clear();
  liveResponseStore.startRun("run-pdf-progress");
  liveResponseStore.startMessage("assistant-run-pdf-progress");
  liveResponseStore.append("assistant-run-pdf-progress", "正在读取扫描 PDF。".repeat(30));
  liveResponseStore.completeMessage("assistant-run-pdf-progress");
  expect(liveResponseStore.getSnapshot()).toMatchObject({ visible: true, status: "streaming" });
  liveResponseStore.hideForTool();
  expect(liveResponseStore.getSnapshot()).toMatchObject({ visible: false, status: "streaming" });
  liveResponseStore.append("assistant-run-pdf-progress", "最终整理结果。");
  liveResponseStore.completeRun();
  expect(liveResponseStore.getSnapshot()).toMatchObject({ visible: true, status: "complete", text: "最终整理结果。" });
  liveResponseStore.clear();
});
