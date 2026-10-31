// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: vi.fn(),
}));
vi.mock("next/link", () => ({
  default: ({ children, ...props }: { children?: unknown }) => <a {...props}>{children as never}</a>,
}));

import { AssistantHeaderAvatar, botAccent } from "../src/components/bot-rail";
import { BOT_SHAPES } from "../src/components/create-bot-dialog";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function shapePath(id: string): string {
  const shape = BOT_SHAPES.find((item) => item.id === id);
  if (!shape) throw new Error(`missing shape ${id}`);
  return shape.path;
}

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
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
}

describe("assistant header avatar matches the rail identity", () => {
  it("draws the bot's saved shape and color, never its initial", async () => {
    await render(
      <AssistantHeaderAvatar
        bot={{ specialistId: "specialist_star", appearance: { color: "#c82779", avatar: "star" } }}
      />,
    );
    const avatar = host.querySelector(".task-context-bot-avatar [data-avatar='shape']");
    expect(avatar?.getAttribute("data-shape")).toBe("star");
    expect(avatar?.querySelector("path")?.getAttribute("d")).toBe(shapePath("star"));
    expect(avatar?.querySelector("path")?.getAttribute("fill")).toBe("#c82779");
    expect(avatar?.textContent).toBe("");
  });

  it("falls back to blob when no appearance was saved", async () => {
    await render(<AssistantHeaderAvatar bot={{ specialistId: "specialist_plain" }} />);
    const avatar = host.querySelector("[data-avatar='shape']");
    expect(avatar?.getAttribute("data-shape")).toBe("blob");
    expect(avatar?.querySelector("path")?.getAttribute("d")).toBe(shapePath("blob"));
    // 老 bot 仍按 id 取稳定强调色。
    expect(avatar?.querySelector("path")?.getAttribute("fill")).toBe(botAccent("specialist_plain"));
  });

  it("falls back to blob for an unknown avatar id", async () => {
    await render(
      <AssistantHeaderAvatar
        bot={{ specialistId: "specialist_odd", appearance: { color: "#0d9655", avatar: "hexagon" } }}
      />,
    );
    expect(host.querySelector("[data-shape]")?.getAttribute("data-shape")).toBe("blob");
    expect(host.textContent).toBe("");
  });

  it("keeps the main assistant as a K letter and never adopts a bot shape", async () => {
    await render(<AssistantHeaderAvatar bot={null} />);
    const avatar = host.querySelector("[data-avatar='letter']");
    expect(avatar?.textContent).toBe("K");
    expect(avatar?.querySelector("svg")).toBeNull();
    expect(avatar?.getAttribute("data-shape")).toBeNull();
    expect(getComputedStyle(avatar as Element).backgroundColor).toBe("rgb(18, 111, 212)");

    // 从子助理会话切回主助理：外形不残留。
    await render(
      <AssistantHeaderAvatar
        bot={{ specialistId: "specialist_star", appearance: { color: "#c82779", avatar: "star" } }}
      />,
    );
    await render(<AssistantHeaderAvatar bot={null} />);
    expect(host.querySelector("path")).toBeNull();
    expect(host.querySelector("[data-avatar='letter']")?.textContent).toBe("K");
  });
});
