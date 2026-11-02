// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
  saveSpecialistCredential: vi.fn(),
}));

import { BotEditDialog } from "../src/components/bot-edit-dialog";
import { BOT_SHAPES } from "../src/lib/bot-appearance";

const source = readFileSync(join(process.cwd(), "src/components/bot-edit-dialog.tsx"), "utf8");

const PALETTE = {
  colors: [
    "#ffffff", "#a9734f", "#ff3b30", "#ff6a00", "#ff9f0a", "#25c06a",
    "#0d9381", "#126fd4", "#7442d1", "#ff2d95", "#8e8e93", "#c82779",
  ],
  avatars: BOT_SHAPES.map(shape => shape.id),
};

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

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

async function flush(rounds = 6) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function detail() {
  return {
    specialist: {
      specialistId: "specialist_demo",
      name: "演示 Bot",
      slug: "demo",
      description: "盯信号",
      agentName: "agent",
      agentVersion: "1.0",
      status: "needs_input",
      revision: 2,
      config: { appearance: { color: "#ff2d95", avatar: "star" } },
      createdAt: "2026-11-03T00:00:00Z",
      updatedAt: "2026-11-03T00:00:00Z",
    },
    duties: [],
  };
}

function buttonByText(text: string): HTMLButtonElement | null {
  return [...host.querySelectorAll("button")].find(node => node.textContent?.trim() === text) ?? null;
}

async function mount() {
  assistantRequest.mockImplementation(async (path: string, method = "GET") => {
    if (path === "specialists/appearance/palette") return PALETTE;
    if (path === "specialists/specialist_demo" && method === "GET") return detail();
    if (path === "specialists/specialist_demo/profile") return detail().specialist;
    return detail();
  });
  await act(async () => {
    root!.render(
      <BotEditDialog specialistId="specialist_demo" onClose={() => {}} onSaved={() => {}} />,
    );
  });
  await flush();
}

describe("bot edit dialog appearance picker", () => {
  it("renders the palette the server published", async () => {
    await mount();
    const swatches = [...host.querySelectorAll("button[aria-label^='颜色 ']")];
    expect(swatches).toHaveLength(PALETTE.colors.length);
    // 已保存的颜色被选中。
    const selected = swatches.find(node => node.getAttribute("data-selected") === "true");
    expect(selected?.getAttribute("aria-label")).toBe("颜色 #ff2d95");
  });

  it("sends the chosen appearance through the profile API", async () => {
    await mount();
    const swatches = [...host.querySelectorAll<HTMLButtonElement>("button[aria-label^='颜色 ']")];
    await act(async () => swatches[1]!.click());
    await flush();
    await act(async () => buttonByText("保存")!.click());
    await flush();
    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/profile"),
    );
    expect(call).toBeTruthy();
    expect(call![1]).toBe("POST");
    expect((call![2] as { appearance: unknown }).appearance).toEqual({
      color: PALETTE.colors[1],
      avatar: "star",
    });
  });

  it("reads the palette from the server instead of a second local colour list", () => {
    expect(source).toContain("useAppearancePalette");
    expect(source).not.toContain("BOT_COLORS");
  });
});
