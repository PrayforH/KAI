// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: { children?: unknown }) => <a {...props}>{children as never}</a>,
}));

import { BotRail } from "../src/components/bot-rail";
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

describe("bot rail no longer distinguishes enabled state", () => {
  it("renders paused and running bots with identical rows", async () => {
    await mount([pausedActive, pausedPending, normalActive]);

    const rows = [...host.querySelectorAll(".bot-rail-item")].filter((node) =>
      node.textContent,
    );
    expect(rows.length).toBeGreaterThan(0);
    for (const node of rows) {
      expect(node.getAttribute("data-paused")).toBeNull();
      expect(node.getAttribute("title")).not.toBe("已暂停 · 无人查看");
      expect(node.querySelector(".bot-rail-label small")).toBeNull();
      expect(node.textContent ?? "").not.toContain("已暂停");
    }
  });

  it("keeps only the bot name in each row", async () => {
    await mount([pausedActive, normalActive]);
    const byName = (name: string) =>
      [...host.querySelectorAll(".bot-rail-item")].find((node) =>
        (node.textContent ?? "").includes(name),
      ) ?? null;
    expect(byName("休息提醒")?.querySelector(".bot-rail-label")?.textContent).toBe("休息提醒");
    expect(byName("行业快报监控")?.getAttribute("data-paused")).toBeNull();
  });
});
