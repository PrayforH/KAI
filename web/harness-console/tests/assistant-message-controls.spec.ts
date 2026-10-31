import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const css = readFileSync(join(process.cwd(), "src/app/web-codex.css"), "utf8");
const agentSource = readFileSync(join(process.cwd(), "src/components/agent-thread.tsx"), "utf8");

it("keeps the answer and its controls in one bottom-aligned flex row on every surface", () => {
  expect(css).toMatch(
    /\.chat-stage \.assistant-answer-row \{[^}]*display:\s*flex;[^}]*align-items:\s*flex-end;/,
  );
  expect(css).not.toContain('.chat-stage[data-surface="assistant"] .assistant-answer-row');
  expect(css).toMatch(
    /\.chat-stage \.assistant-message-controls \{[^}]*position:\s*static;[^}]*flex:\s*0 0 auto;/,
  );
});

it("shrinks task answers to their content so the controls hug the bubble", () => {
  expect(css).toMatch(
    /\.chat-stage:not\(\[data-surface="assistant"\]\) \.harness-assistant-message \.assistant-answer \{[^}]*width:\s*fit-content;[^}]*max-width:\s*min\(640px, 92%\);/,
  );
});

it("keeps the composer centered inside the reading column instead of bleeding sideways", () => {
  expect(css).toMatch(
    /\.aui-thread-viewport-footer \{[^}]*box-sizing:\s*border-box;[^}]*margin-inline:\s*auto;/,
  );
  expect(css).toMatch(
    /\.harness-composer-shell \{[^}]*margin-inline:\s*auto;/,
  );
});

it("wraps the answer and action bar before any sibling blocks", () => {
  const row = agentSource.indexOf('className="assistant-answer-row"');
  const answer = agentSource.indexOf("<AssistantResponse", row);
  const controls = agentSource.indexOf('className="assistant-message-controls"', row);
  const followup = agentSource.indexOf('className="followup-input-anchor"', row);
  expect(row).toBeGreaterThan(-1);
  expect(answer).toBeGreaterThan(row);
  expect(controls).toBeGreaterThan(answer);
  expect(followup).toBeGreaterThan(controls);
});
