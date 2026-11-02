// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: { children?: unknown }) => <a {...props}>{children as never}</a>,
}));

import { BotRail, isUnviewedPaused } from "../src/components/bot-rail";
import type { Specialist } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function specialist(
  overrides: Partial<Specialist> & { specialistId: string; name: string },
): Specialist {
  return {
    slug: overrides.specialistId,
    description: "职责说明",
    agentName: "audit-agent",
    agentVersion: "1.0.0",
    status: "active",
    revision: 1,
    config: {},
    createdAt: "2026-10-01T00:00:00Z",
    updatedAt: "2026-10-01T00:00:00Z",
    ...overrides,
  } as Specialist;
}

const pausedActive = specialist({
  specialistId: "specialist_paused_active",
  name: "休息提醒",
  routinePausedReason: { reason: "unviewed", hours_since_view: 30 },
});
const pausedPending = specialist({
  specialistId: "specialist_paused_pending",
  name: "天气提醒",
  status: "needs_input",
  routinePausedReason: { reason: "unviewed", hours_since_view: 26 },
});
const normalActive = specialist({
  specialistId: "specialist_normal",
  name: "行业快报监控",
});
const otherReason = specialist({
  specialistId: "specialist_other_reason",
  name: "示例 Bot",
  routinePausedReason: { reason: "manual" },
});

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  assistantRequest.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

async function mount(list: Specialist[]) {
  assistantRequest.mockResolvedValue(list);
  await act(async () => {
    root!.render(<BotRail currentThreadId="assistant" />);
  });
  await act(async () => {});
}

describe("isUnviewedPaused", () => {
  it("only treats reason=unviewed as paused", () => {
    expect(isUnviewedPaused(pausedActive)).toBe(true);
    expect(isUnviewedPaused(normalActive)).toBe(false);
    expect(isUnviewedPaused(otherReason)).toBe(false);
  });
});

describe("bot rail paused badge", () => {
  it("marks every unviewed-paused bot in both groups without adding a second line", async () => {
    await mount([pausedActive, normalActive, pausedPending, otherReason]);

    const paused = [...host.querySelectorAll('.bot-rail-item[data-paused="true"]')];
    expect(paused).toHaveLength(2);
    const pausedNames = paused.map(
      (node) => node.querySelector(".bot-rail-label strong")?.textContent,
    );
    expect(pausedNames).toEqual(["休息提醒", "天气提醒"]);
    for (const node of paused) {
      // 侧栏只显示名称；暂停提示走 title，不占副行。
      expect(node.querySelector(".bot-rail-label small")).toBeNull();
      expect(node.getAttribute("title")).toBe("已暂停 · 无人查看");
      expect(node.textContent ?? "").not.toContain("已暂停");
    }
  });

  it("leaves normal bots and non-unviewed reasons unmarked", async () => {
    await mount([pausedActive, normalActive, otherReason]);

    const byName = (name: string) =>
      [...host.querySelectorAll(".bot-rail-item")].find((node) =>
        (node.textContent ?? "").includes(name),
      ) ?? null;
    const normal = byName("行业快报监控");
    expect(normal?.getAttribute("data-paused")).toBeNull();
    expect(normal?.getAttribute("title")).toBeNull();
    expect(normal?.querySelector(".bot-rail-label")?.textContent).toBe("行业快报监控");

    const other = byName("示例 Bot");
    expect(other?.getAttribute("data-paused")).toBeNull();
    expect(other?.querySelector(".bot-rail-label")?.textContent).toBe("示例 Bot");
  });

  it("never marks the main assistant entry", async () => {
    await mount([pausedActive]);
    const main = host.querySelector('.bot-rail-item[href="/assistant"]');
    expect(main).not.toBeNull();
    expect(main?.getAttribute("data-paused")).toBeNull();
    expect(main?.querySelector(".bot-rail-label")?.textContent).toBe("主助理");
    expect(main?.textContent).not.toContain("已暂停");
  });
});
