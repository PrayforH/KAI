import { readFileSync } from "node:fs";
import { join } from "node:path";
import postcss from "postcss";
import { expect, it } from "vitest";

const read = (file: string) => readFileSync(join(process.cwd(), "src/app", file), "utf8");
const assistant = read("assistant-conversation.css");

it("bounds every Bot presentation rule to the assistant shell", () => {
  const rules: string[] = [];
  postcss.parse(assistant).walkRules(rule => {
    if (rule.parent?.type === "atrule" && rule.parent.name === "keyframes") return;
    if (rule.selector === ":root" || rule.selector === 'html[data-color-mode="light"]') {
      // Shared theme tokens do not style task/project geometry. Every actual
      // presentation rule must still be bounded to the assistant surface.
      rule.walkDecls(decl => expect(decl.prop.startsWith("--kai-bot-neutral-")).toBe(true));
      return;
    }
    rules.push(rule.selector);
    expect(rule.selectors.every(selector => selector.includes('.console-shell[data-conversation-surface="assistant"]'))).toBe(true);
    expect(rule.selector).not.toContain('body.codex-theme-v1:has');
  });
  expect(rules.length).toBeGreaterThan(30);
  expect(read("web-codex.css")).not.toContain('[data-surface="assistant"]');
});

it("keeps Bot reply geometry out of task and project replies", () => {
  expect(assistant).toMatch(/\.chat-stage\[data-surface="assistant"\] \.assistant-answer-row \{[^}]*display:\s*flex;/);
  expect(read("web-codex.css")).not.toContain('.chat-stage:not([data-surface="assistant"]) .harness-assistant-message .assistant-answer');
  expect(read("codex-theme.css")).toMatch(/\.assistant-answer \{[^}]*width:\s*100%;/);
});
