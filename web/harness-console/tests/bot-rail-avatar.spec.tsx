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

import { BotRail, BotRailAvatar } from "../src/components/bot-rail";
import { BOT_SHAPES } from "../src/components/create-bot-dialog";
import type { Specialist } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function shapePath(id: string): string {
  const shape = BOT_SHAPES.find((item) => item.id === id);
  if (!shape) throw new Error(`missing shape ${id}`);
  return shape.path;
}

function specialist(
  overrides: Partial<Specialist> & { specialistId: string; name: string },
): Specialist {
  return {
    slug: overrides.specialistId,
    description: "",
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

const starBot = specialist({
  specialistId: "specialist_star",
  name: "星标记",
  config: { appearance: { color: "#c82779", avatar: "star" } },
});
const dropBot = specialist({
  specialistId: "specialist_drop",
  name: "水滴记",
  status: "needs_input",
  config: { appearance: { color: "#0d9655", avatar: "drop" } },
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

async function render(node: React.ReactElement) {
  await act(async () => {
    root!.render(node);
  });
  await act(async () => {});
}

describe("BotRailAvatar renders saved appearance shapes", () => {
  it("draws the chosen shape path with the bot color and no letter text", async () => {
    await render(<BotRailAvatar tone="#c82779" shape="star" />);
    const path = host.querySelector('[data-avatar="shape"] path');
    expect(path?.getAttribute("d")).toBe(shapePath("star"));
    expect(path?.getAttribute("fill")).toBe("#c82779");
    expect(host.querySelector('[data-avatar="shape"]')?.textContent).toBe("");
  });

  it("falls back to blob when no appearance was saved", async () => {
    await render(<BotRailAvatar tone="#126fd4" />);
    expect(host.querySelector("path")?.getAttribute("d")).toBe(shapePath("blob"));
    expect(host.querySelector('[data-shape="blob"]')).not.toBeNull();
  });

  it("falls back to blob for an unknown avatar id instead of a letter", async () => {
    await render(<BotRailAvatar tone="#7442d1" shape="unknown-shape" />);
    expect(host.querySelector("path")?.getAttribute("d")).toBe(shapePath("blob"));
    expect(host.textContent).toBe("");
  });
});

describe("BotRail uses the shape avatars for every bot group", () => {
  it("renders both in-post and pending bots with their own shapes; main assistant keeps K", async () => {
    assistantRequest.mockResolvedValue([starBot, dropBot]);
    await render(<BotRail currentThreadId="assistant" />);

    const avatars = [...host.querySelectorAll('[data-avatar="shape"]')];
    expect(avatars.map((node) => node.getAttribute("data-shape"))).toEqual(["star", "drop"]);
    expect(avatars.map((node) => node.textContent)).toEqual(["", ""]);
    expect(avatars[0]?.querySelector("path")?.getAttribute("d")).toBe(shapePath("star"));
    expect(avatars[1]?.querySelector("path")?.getAttribute("fill")).toBe("#0d9655");

    const mainAvatar = host.querySelector('[data-avatar="letter"]');
    expect(mainAvatar?.textContent).toBe("K");
    expect(mainAvatar?.querySelector("svg")).toBeNull();

    const text = host.textContent ?? "";
    expect(text).toContain("可接单");
    expect(text).toContain("需要关注");
    expect(text).toContain("主助理");
  });

  it("does not paint a bot's shape onto the main assistant entry", async () => {
    assistantRequest.mockResolvedValue([starBot]);
    await render(<BotRail currentThreadId="assistant" />);
    const mainAvatar = host.querySelector('[data-avatar="letter"]');
    expect(mainAvatar?.innerHTML).not.toContain("path");
    // 主助理入口仍可点击回 /assistant
    expect(host.querySelector('a[href="/assistant"]')).not.toBeNull();
  });
});
