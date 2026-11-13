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
  const webCodexCss = source("src/app/web-codex.css");
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

  it("removes the keyboard hint from both surfaces", () => {
    expect(groupComposer).not.toContain("Enter 发送");
    expect(agentThread).not.toContain("Enter 发送消息");
    expect(source("src/lib/composer-interactions.ts")).not.toContain('"/help"');
  });

  it("puts send on the left for assistant and group composers", () => {
    expect(agentThread).toContain("composer-footer--action-first");
    expect(agentThread.indexOf("{assistantSurface ? composerAction : null}"))
      .toBeLessThan(agentThread.indexOf("<div className=\"composer-toolbar\">"));
    expect(source("src/app/conversation-controls.css"))
      .toContain("grid-template-columns: 32px minmax(0, 1fr)");
    // 群聊：发送按钮位于 textarea 之前。
    const send = groupComposer.indexOf('className={styles.send}');
    const textarea = groupComposer.indexOf("<textarea");
    expect(send).toBeGreaterThan(0);
    expect(send).toBeLessThan(textarea);
  });

  it("keeps the assistant composer at one row and suppresses the inner focus ring", () => {
    expect(agentThread).toContain("Math.max(1, composerText.split");
    expect(agentThread).not.toContain("Math.max(2, composerText.split");
    const css = source("src/components/agent-composer.module.css");
    expect(css).toMatch(/\.input:focus,\s*\.input:focus-visible\s*\{[^}]*outline: none !important/);
  });

  it("widens both conversation columns to 1100px", () => {
    expect(groupChatCss).toContain("max-width: 1100px");
    expect(groupComposerCss).toContain("max-width: 1100px");
    expect(webCodexCss).toContain("--aui-thread-max-width: 1100px");
    expect(webCodexCss).toContain("max-width: 1100px");
  });

  it("drops the assistant header and keeps the floating entries", () => {
    expect(page).toContain("{!assistantView && <header className=\"console-header\">");
    expect(page).toContain('className="assistant-floating-controls"');
    expect(source("src/components/task-sidebar.tsx")).toContain("task-rail-toggle");
  });
});
