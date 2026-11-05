// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: vi.fn()})}));
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
    description: "每天汇总行业动态并推送站内摘要",
    agentName: "archive-assistant-agent",
    agentVersion: "0.1.1",
    status: "active",
    revision: 1,
    config: {},
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
    ...overrides,
  } as Specialist;
}

const active = specialist({ specialistId: "specialist_active", name: "行业快报监控" });
const pending = specialist({
  specialistId: "specialist_pending",
  name: "天气提醒",
  status: "needs_input",
});
const paused = specialist({
  specialistId: "specialist_paused",
  name: "休息提醒",
  routinePausedReason: { reason: "unviewed", hours_since_view: 30 },
});

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  assistantRequest.mockReset();
  assistantRequest.mockResolvedValue([active, pending, paused]);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

async function mount() {
  await act(async () => {
    root!.render(<BotRail currentThreadId="assistant" />);
  });
  await act(async () => {});
}

describe("bot rail shows names only", () => {
  it("renders no secondary copy under any rail item", async () => {
    await mount();
    const labels = [...host.querySelectorAll(".bot-rail-item .bot-rail-label")].map(
      (node) => node.textContent,
    );
    expect(labels).toEqual(["主助理", "行业快报监控", "休息提醒", "天气提醒"]);

    const text = host.textContent ?? "";
    expect(text).not.toContain("统一接活");
    expect(text).not.toContain("archive-assistant-agent");
    expect(text).not.toContain("每天汇总行业动态");
    expect(host.querySelectorAll(".bot-rail-label small")).toHaveLength(0);
  });

  it("keeps the paused marker on the row without a description line", async () => {
    await mount();
    const row = [...host.querySelectorAll(".bot-rail-item")].find((node) =>
      (node.textContent ?? "").includes("休息提醒"),
    )!;
    expect(row.getAttribute("data-paused")).toBe("true");
    expect(row.getAttribute("title")).toBe("已暂停 · 无人查看");
    expect(row.querySelector(".bot-rail-label")?.textContent).toBe("休息提醒");
    expect(row.querySelector(".bot-rail-label small")).toBeNull();
  });
});
