// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupMessage } from "../src/components/assistant-group-message";
import { MessageActions } from "../src/components/message-actions";
import {
  findMessageAnchor,
  isMessageAnchorVisible,
  MIN_VISIBLE_ANCHOR_HEIGHT,
  visibleHeightWithin,
} from "../src/lib/message-anchor-visibility";
import type { GroupMessage } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const BODY_HEIGHT = 200;
const SCROLLER_TOP = 100;
const SCROLLER_BOTTOM = 500;

function rect(top: number, bottom: number): DOMRect {
  return {
    top,
    bottom,
    left: 0,
    right: 0,
    width: 0,
    height: Math.max(0, bottom - top),
    x: 0,
    y: top,
    toJSON: () => ({}),
  } as DOMRect;
}

let root: Root | undefined;
let host: HTMLDivElement;
let scroller: HTMLDivElement;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  scroller = document.createElement("div");
  scroller.style.overflowY = "auto";
  host.append(scroller);
  root = createRoot(scroller);
});

afterEach(async () => {
  vi.restoreAllMocks();
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

function message(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    messageId: "m1",
    groupId: "g1",
    author: "user",
    authorName: "我",
    specialistId: null,
    text: "一条会滚出上沿的消息",
    runId: null,
    replyToMessageId: null,
    reactions: [],
    status: "completed",
    createdAt: "2026-11-13T05:00:00Z",
    ...overrides,
  };
}

/** 气泡高 200px，只按 visibleHeight 露出在滚动区顶部。 */
function mockGeometry(visibleHeight: number) {
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      if (this === scroller) return rect(SCROLLER_TOP, SCROLLER_BOTTOM);
      if (this.hasAttribute("data-message-body")) {
        const bottom = SCROLLER_TOP + visibleHeight;
        return rect(bottom - BODY_HEIGHT, bottom);
      }
      return rect(0, 0);
    },
  );
}

async function render(node: ReactNode) {
  await act(async () => root!.render(node));
  // 锚点解析与首帧测量都在 effect 里，多跑一次把状态落定。
  await act(async () => undefined);
}

function actionBar(): HTMLElement | null {
  return host.querySelector<HTMLElement>("[data-suppressed]");
}

describe("isMessageAnchorVisible", () => {
  const scrollerEdges = { top: SCROLLER_TOP, bottom: SCROLLER_BOTTOM };

  it("hides at 0px and 12px, shows at 24px and 100px", () => {
    const anchor = (visibleHeight: number) => {
      const bottom = SCROLLER_TOP + visibleHeight;
      return { top: bottom - BODY_HEIGHT, bottom };
    };

    expect(visibleHeightWithin(anchor(0), scrollerEdges)).toBe(0);
    expect(visibleHeightWithin(anchor(12), scrollerEdges)).toBe(12);
    expect(visibleHeightWithin(anchor(24), scrollerEdges)).toBe(24);
    expect(visibleHeightWithin(anchor(100), scrollerEdges)).toBe(100);

    expect(isMessageAnchorVisible(anchor(0), scrollerEdges)).toBe(false);
    expect(isMessageAnchorVisible(anchor(12), scrollerEdges)).toBe(false);
    expect(isMessageAnchorVisible(anchor(24), scrollerEdges)).toBe(true);
    expect(isMessageAnchorVisible(anchor(100), scrollerEdges)).toBe(true);
    expect(MIN_VISIBLE_ANCHOR_HEIGHT).toBe(24);
  });
});

describe("message action bar follows its bubble", () => {
  it("suppresses the bar when the bubble shows 0px and 12px", async () => {
    for (const visibleHeight of [0, 12]) {
      mockGeometry(visibleHeight);
      await render(
        <AssistantGroupMessage message={message()} tone="#6b746e" onReact={vi.fn()} onReply={vi.fn()} />,
      );
      expect(actionBar()?.getAttribute("data-suppressed")).toBe("true");
      await act(async () => root!.render(null));
    }
  });

  it("shows the bar when the bubble shows 24px or more", async () => {
    for (const visibleHeight of [24, 100]) {
      mockGeometry(visibleHeight);
      await render(
        <AssistantGroupMessage message={message()} tone="#6b746e" onReact={vi.fn()} onReply={vi.fn()} />,
      );
      expect(actionBar()?.getAttribute("data-suppressed")).toBe("false");
      await act(async () => root!.render(null));
    }
  });

  it("reveals on bubble hover, not on row hover", async () => {
    mockGeometry(100);
    await render(
      <AssistantGroupMessage message={message()} tone="#6b746e" onReact={vi.fn()} onReply={vi.fn()} />,
    );
    const body = host.querySelector<HTMLElement>("[data-message-body]")!;
    const bar = actionBar()!;
    expect(bar.getAttribute("data-visible")).toBe("false");

    await act(async () => body.dispatchEvent(new Event("pointerenter")));
    expect(bar.getAttribute("data-visible")).toBe("true");

    await act(async () =>
      body.dispatchEvent(new MouseEvent("pointerleave", { relatedTarget: null })),
    );
    expect(bar.getAttribute("data-visible")).toBe("false");
  });

  it("applies the same rule to the shared bar used by the single assistant", async () => {
    mockGeometry(12);
    await render(
      <div>
        <div data-message-body="true">回答正文</div>
        <MessageActions copyText="回答正文" />
      </div>,
    );
    expect(actionBar()?.getAttribute("data-suppressed")).toBe("true");

    mockGeometry(100);
    await act(async () => root!.render(null));
    await render(
      <div>
        <div data-message-body="true">回答正文</div>
        <MessageActions copyText="回答正文" />
      </div>,
    );
    expect(actionBar()?.getAttribute("data-suppressed")).toBe("false");
  });

  it("resolves the nearest single message body and refuses to guess across messages", () => {
    const outer = document.createElement("div");
    outer.innerHTML = `
      <div id="row"><div data-message-body="true"></div></div>
      <div id="ambiguous"><div data-message-body="true"></div><div data-message-body="true"></div></div>
    `;
    const actions = document.createElement("div");
    outer.querySelector("#row")!.append(actions);
    expect(findMessageAnchor(actions)?.getAttribute("data-message-body")).toBe("true");
    const ambiguousBody = outer.querySelector<HTMLElement>("#ambiguous > div:last-child");
    expect(findMessageAnchor(ambiguousBody)).toBeNull();
  });
});

describe("action bar placement contract", () => {
  it("keeps the bar in normal flow instead of floating over the blank area", () => {
    const sharedCss = readFileSync(
      resolve(process.cwd(), "src/components/message-actions.module.css"),
      "utf8",
    );
    const groupCss = readFileSync(
      resolve(process.cwd(), "src/components/assistant-group-message.module.css"),
      "utf8",
    );
    const actionsBlock = sharedCss.slice(sharedCss.indexOf(".actions {"), sharedCss.indexOf(".actionButtons"));
    expect(actionsBlock).toContain("position: relative");
    expect(actionsBlock).not.toMatch(/position:\s*(absolute|fixed)/);
    expect(sharedCss).toContain('.actions[data-suppressed="true"]');
    expect(sharedCss).toContain("visibility: hidden");
    expect(sharedCss).toContain("pointer-events: none");
    // 整行 hover 是悬空的直接原因，必须移除。
    expect(groupCss).not.toContain(".userRow:hover .actions");
    expect(groupCss).not.toContain(".memberRow:hover .actions");
  });

  it("marks the single-assistant answer and user bubble as anchors", () => {
    const agentThreadSource = readFileSync(
      resolve(process.cwd(), "src/components/agent-thread.tsx"),
      "utf8",
    );
    const sentUserSource = readFileSync(
      resolve(process.cwd(), "src/components/sent-user-content.tsx"),
      "utf8",
    );
    expect(agentThreadSource).toMatch(/className="assistant-answer"[\s\S]{0,120}data-message-body="true"/);
    expect(sentUserSource).toMatch(/className="sent-user-bubble"[\s\S]{0,80}data-message-body="true"/);
  });
});
