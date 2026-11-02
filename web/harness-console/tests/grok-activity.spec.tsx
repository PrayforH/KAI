import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActivityPhrase, GrokFace, GrokActivityRow } from "../src/components/grok-activity";

// 中文短语现在按字素分包渲染，断言前去掉标签再比对文本。
const plainText = (html: string) => html.replace(/<[^>]+>/g, "");

describe("GrokActivityRow", () => {
  it("renders the face with a state and the phrase words", () => {
    const html = renderToStaticMarkup(
      <GrokActivityRow phrase="探索本机部署状态" state="working" />,
    );
    expect(html).toContain('data-state="working"');
    expect(html).not.toContain("grok-face");
    expect(html).toContain('role="status"');
    expect(plainText(html)).toContain("探索本机部署状态");
  });

  it("server-renders the whole phrase so reloads never truncate history", () => {
    // 渐现只发生在客户端动画里；SSR/历史渲染必须一次性给出完整短语。
    const html = renderToStaticMarkup(<ActivityPhrase text="正在读取文件" />);
    expect(plainText(html)).toContain("正在读取文件");
  });

  it("keeps the face decorative", () => {
    const html = renderToStaticMarkup(<GrokFace state="idle" />);
    expect(html).toContain('aria-hidden="true"');
  });
});
