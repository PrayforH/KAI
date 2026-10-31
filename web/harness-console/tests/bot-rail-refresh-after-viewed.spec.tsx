// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
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

import { BotRail } from "../src/components/bot-rail";
import type { Specialist } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function specialist(overrides: Partial<Specialist> & { specialistId: string; name: string }): Specialist {
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

const paused = specialist({
  specialistId: "specialist_pause",
  name: "休息提醒",
  routinePausedReason: { reason: "unviewed", hours_since_view: 30 },
});
const cleared = specialist({ specialistId: "specialist_pause", name: "休息提醒" });

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

describe("bot rail refreshes as soon as viewed clears the pause", () => {
  it("drops the paused badge when the page reports a successful view", async () => {
    assistantRequest
      .mockResolvedValueOnce([paused])
      .mockResolvedValue([cleared]);
    await act(async () => {
      root!.render(<BotRail currentThreadId="assistant" />);
    });
    await act(async () => {});

    expect(host.querySelector('.bot-rail-item[data-paused="true"]')).not.toBeNull();
    expect(host.textContent ?? "").toContain("已暂停 · 无人查看");

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("kai:specialists-changed", {
          detail: { specialistId: "specialist_pause", reason: "viewed" },
        }),
      );
    });
    await act(async () => {});

    expect(host.querySelector('.bot-rail-item[data-paused="true"]')).toBeNull();
    expect(host.textContent ?? "").not.toContain("已暂停");
    // 重拉后恢复原描述，而不是留一条空的暂停行。
    expect(host.textContent ?? "").toContain("职责说明");
  });

  it("keeps the old list (and stops listening) on unmount", async () => {
    await mount([paused]);
    const callsAfterMount = assistantRequest.mock.calls.length;
    expect(callsAfterMount).toBeGreaterThan(0);

    await act(async () => {
      root!.unmount();
    });
    root = undefined;
    window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
    await act(async () => {});
    expect(assistantRequest.mock.calls.length).toBe(callsAfterMount);
  });
});

describe("viewed success is what triggers the refresh", () => {
  it("the assistant page dispatches the same event only after the viewed POST resolves", () => {
    const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
    // 事件名必须与 rail 监听的一致；派发挂在 viewed 的 .then 上（失败不会触发）。
    expect(page).toContain('"kai:specialists-changed"');
    expect(page).toMatch(
      /specialists\/\$\{encodeURIComponent\(botId\)\}\/viewed[\s\S]{0,220}\.then\(\(\) => \{[\s\S]{0,200}kai:specialists-changed/,
    );
    expect(page).toContain('.catch(() => { /* 查看上报失败不影响会话 */ });');
  });
});
