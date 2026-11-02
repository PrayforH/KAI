import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AssistantBotProvider } from "../src/components/assistant-bot-context";
import { BOT_SHAPES } from "../src/components/create-bot-dialog";
import { GrokActivityRow, GrokFace } from "../src/components/grok-activity";

function shapePath(id: string): string {
  const shape = BOT_SHAPES.find((item) => item.id === id);
  if (!shape) throw new Error(`missing shape ${id}`);
  return shape.path;
}

const starBot = {
  specialistId: "specialist_star",
  name: "星标",
  description: "",
  status: "active",
  threadId: "thread-star",
  objectiveId: "objective-star",
  appearance: { color: "#c82779", avatar: "star" },
};

describe("GrokFace draws the bot's saved shape", () => {
  it("uses the chosen shape path and marks the avatar id", () => {
    const html = renderToStaticMarkup(<GrokFace state="working" avatar="star" />);
    expect(html).toContain('data-avatar="star"');
    expect(html).toContain(`d="${shapePath("star")}"`);
    expect(html).not.toContain('d="M32 6c11');
  });

  it("falls back to blob when no avatar was saved or the id is unknown", () => {
    const none = renderToStaticMarkup(<GrokFace state="idle" />);
    expect(none).toContain('data-avatar="blob"');
    expect(none).toContain(`d="${shapePath("blob")}"`);

    const unknown = renderToStaticMarkup(<GrokFace state="idle" avatar="unknown-shape" />);
    expect(unknown).toContain('data-avatar="blob"');
    expect(unknown).toContain(`d="${shapePath("blob")}"`);
  });

  it("keeps the eyes, blink and aria-hidden in every state", () => {
    const idle = renderToStaticMarkup(<GrokFace state="idle" avatar="drop" />);
    expect((idle.match(/<ellipse class="grok-face-eye"/g) ?? []).length).toBe(2);
    expect(idle).toContain('data-blink="true"');
    expect(idle).toContain('aria-hidden="true"');

    const working = renderToStaticMarkup(<GrokFace state="working" avatar="drop" />);
    expect((working.match(/<ellipse class="grok-face-eye"/g) ?? []).length).toBe(2);
    expect(working).not.toContain('data-blink="true"');
    expect(working).toContain('data-avatar="drop"');
  });

  it("reads the current bot shape from context when no prop is given", () => {
    const html = renderToStaticMarkup(
      <AssistantBotProvider bot={starBot}>
        <GrokFace state="working" />
      </AssistantBotProvider>,
    );
    expect(html).toContain('data-avatar="star"');
    expect(html).toContain(`d="${shapePath("star")}"`);

    // 主助理（无 bot）：保持 blob，不被任何子助理外形污染。
    const assistant = renderToStaticMarkup(
      <AssistantBotProvider bot={null}>
        <GrokFace state="working" />
      </AssistantBotProvider>,
    );
    expect(assistant).toContain('data-avatar="blob"');
  });

  it("keeps the status text-only even when a Bot avatar is configured", () => {
    const html = renderToStaticMarkup(
      <GrokActivityRow phrase="继续盯梢本轮：" state="working" avatar="pebble" />,
    );
    expect(html).not.toContain("grok-face");
    expect(html).toContain("继续盯梢本轮");
    expect(html.replace(/<[^>]+>/g, "")).toContain("继续盯梢本轮：");
  });
});
