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

import { BotEditDialog } from "../src/components/bot-edit-dialog";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function detail(overrides: Record<string, unknown> = {}) {
  return {
    specialist: {
      specialistId: "specialist_pause",
      name: "无人查看验收",
      description: "例行程序闸门",
      status: "active",
      revision: 3,
      config: { schedule_minutes: 1440, notify_channel: "inbox" },
      lastViewedAt: "2026-10-29T12:00:00Z",
      routinePausedReason: { reason: "unviewed", hours_since_view: 48 },
      ...overrides,
    },
    duties: [
      {
        objectiveId: "objective_1",
        title: "每天检查一次",
        status: "active",
        schedule: { everyMinutes: 1440 },
      },
    ],
  };
}

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

async function mount(payload: unknown) {
  assistantRequest.mockResolvedValue(payload);
  await act(async () => {
    root!.render(<BotEditDialog specialistId="specialist_pause" onClose={() => {}} onSaved={() => {}} />);
  });
  await act(async () => {});
}

describe("bot edit dialog shows the unviewed routine pause", () => {
  it("reads routinePausedReason from the specialist top level", async () => {
    await mount(detail());
    expect(host.textContent ?? "").toContain("已暂停");
    expect(host.textContent ?? "").toContain("因无人查看已暂停，打开本会话后恢复");
    expect(host.querySelector('[data-paused="true"]')).not.toBeNull();
  });

  it("does not claim a pause when the field is absent", async () => {
    await mount(detail({ routinePausedReason: null }));
    expect(host.textContent ?? "").not.toContain("因无人查看已暂停");
    expect(host.querySelector('[data-paused="true"]')).toBeNull();
    expect(host.textContent ?? "").toContain("已激活");
  });

  it("ignores a stale marker accidentally stored inside config", async () => {
    await mount(
      detail({
        routinePausedReason: null,
        config: {
          schedule_minutes: 1440,
          routinePausedReason: { reason: "unviewed" },
        },
      }),
    );
    expect(host.textContent ?? "").not.toContain("因无人查看已暂停");
  });
});

describe("assistant page marks a view only when a bot conversation loads", () => {
  const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

  it("posts the explicit viewed endpoint once per bot visit", () => {
    expect(page).toContain("/viewed`");
    expect(page).toContain("botViewedRef.current !== botId");
    // 只有拿到真实会话后才上报，避免仅停在空态页也算查看。
    const viewedIndex = page.indexOf("/viewed`");
    const threadIndex = page.indexOf("if (data.threadId) {");
    expect(threadIndex).toBeGreaterThan(-1);
    expect(viewedIndex).toBeGreaterThan(threadIndex);
  });
});
