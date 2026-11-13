import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

/** jsdom 不做样式计算，这里守住群聊与单助理“同一套”的结构前提。 */
function source(path: string) {
  return readFileSync(resolve(process.cwd(), path), "utf8");
}

describe("group and assistant composer parity", () => {
  const groupComposer = source("src/components/assistant-group-composer.tsx");
  const agentThread = source("src/components/agent-thread.tsx");
  const groupChatCss = source("src/components/assistant-group-chat.module.css");
  const groupComposerCss = source("src/components/assistant-group-composer.module.css");
  const assistantCss = source("src/app/assistant-conversation.css");
  const page = source("src/app/page.tsx");

  it("shares one mention picker and one message action bar", () => {
    expect(groupComposer).toContain('from "./mention-picker"');
    expect(agentThread).toContain('from "./mention-picker"');
    expect(groupComposer).toContain("<MentionMenu");
    expect(agentThread).toContain("<MentionMenu");
    expect(source("src/components/assistant-group-message.tsx")).toContain('from "./message-actions"');
    expect(agentThread).toContain('from "./message-actions"');
    expect(agentThread).toContain("<MessageActions");
  });

  it("shares one voice control instead of two implementations", () => {
    expect(groupComposer).toContain('from "./voice-input"');
    expect(groupComposer).toContain("<VoiceInput");
    expect(agentThread).toContain('from "./voice-input"');
    expect(agentThread).toContain("<VoiceInput");
  });

  it("removes the keyboard hint from both surfaces", () => {
    expect(groupComposer).not.toContain("Enter 发送");
    expect(agentThread).not.toContain("Enter 发送消息");
    expect(source("src/lib/composer-interactions.ts")).not.toContain('"/help"');
  });

  it("orders both composers as +, input, voice, send (send rightmost)", () => {
    // 群聊：DOM 顺序 + -> textarea -> 语音 -> 发送。
    const plus = groupComposer.indexOf("添加附件或知识库");
    const textarea = groupComposer.indexOf("<textarea");
    const voice = groupComposer.indexOf("<VoiceInput");
    const send = groupComposer.indexOf("className={styles.send}");
    expect(plus).toBeGreaterThan(0);
    expect(plus).toBeLessThan(textarea);
    expect(textarea).toBeLessThan(voice);
    expect(voice).toBeLessThan(send);

    // 单助理：同一排布，发送不再用 footer 的 action-first 网格放在最左。
    expect(agentThread).not.toContain("composer-footer--action-first");
    expect(agentThread).toContain("composer-assistant-row");
    const model = agentThread.indexOf("{composerModel}");
    const assistantVoice = agentThread.indexOf("{composerVoice}");
    const action = agentThread.indexOf("{composerAction}\n          </div>");
    expect(model).toBeGreaterThan(0);
    expect(model).toBeLessThan(assistantVoice);
    expect(assistantVoice).toBeLessThan(action);
    expect(source("src/app/conversation-controls.css")).not.toContain("composer-footer--action-first");
  });

  it("keeps no @ button on either composer", () => {
    expect(groupComposer).not.toContain('aria-label="提到群成员"');
    expect(agentThread).not.toContain('aria-label="提到群成员"');
  });

  it("restores a fixed surface title on both surfaces without header chrome", () => {
    expect(page.match(/data-testid="assistant-surface-title"/g)).toHaveLength(2);
    const css = assistantCss.match(/\.assistant-surface-title\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(css).toContain("flex: 0 0 auto");
    expect(css).not.toContain("box-shadow");
    expect(css).not.toContain("border-bottom");
    // 仍然不渲染整条顶栏。
    expect(page).toContain("{!assistantView && <header className=\"console-header\">");
  });

  it("keeps the assistant composer at one row and suppresses the inner focus ring", () => {
    expect(agentThread).toContain("Math.max(1, composerText.split");
    expect(agentThread).not.toContain("Math.max(2, composerText.split");
    const css = source("src/components/agent-composer.module.css");
    expect(css).toMatch(/\.input:focus,\s*\.input:focus-visible\s*\{[^}]*outline: none !important/);
  });

  it("puts the actual assistant input area inside 1100–1200 and shrinks on narrow screens", () => {
    expect(groupChatCss).toContain("max-width: 1100px");
    expect(groupComposerCss).toContain("max-width: 1100px");
    // 两侧 72px 的 viewport 留白会吃掉宽度，助理面收窄到 16–24px。
    expect(assistantCss).toContain("padding-inline: clamp(16px, 2vw, 24px)");
    expect(assistantCss).toContain("width: min(1100px, 100%)");
    expect(assistantCss).toContain("max-width: 1100px");
    expect(assistantCss).toContain("@media (max-width: 1240px)");
  });

  it("shows the server's own message on a 403", () => {
    const client = source("src/lib/assistant-client.ts");
    const block = client.slice(
      client.indexOf("if (response.status === 403)"),
      client.indexOf("if (response.status === 409)"),
    );
    expect(block).toContain("value?.detail?.message");
    expect(block).toContain("没有权限执行这个操作");
  });

  it("offers a real file upload in the group + menu", () => {
    expect(groupComposer).toContain("onUploadDocument");
    expect(groupComposer).toContain("上传文件");
    expect(groupComposer).toContain('type="file"');
  });

  it("drops the assistant header and keeps the floating entries", () => {
    expect(page).toContain('className="assistant-floating-controls"');
    expect(source("src/components/task-sidebar.tsx")).toContain("task-rail-toggle");
  });
});
