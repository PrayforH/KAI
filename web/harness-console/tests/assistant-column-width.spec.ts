import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const css = readFileSync(join(process.cwd(), "src/app/assistant-conversation.css"), "utf8");

it("caps the assistant conversation at the Grok standard column", () => {
  expect(css).toMatch(/--chat-column-max:\s*60rem/);
  expect(css).toMatch(/--chat-bubble-max:\s*35rem/);
  expect(css).toMatch(/--chat-prose-max:\s*44rem/);
  // Messages share one centered column instead of stretching the viewport.
  expect(css).toMatch(
    /\.aui-thread-viewport > :not\(\.aui-thread-viewport-footer\)\s*\{[^}]*max-width:\s*var\(--chat-column-max\)/,
  );
  // The footer band caps just past the column; the composer itself is the column.
  expect(css).toMatch(
    /\.aui-thread-viewport-footer\s*\{[^}]*max-width:\s*calc\(var\(--chat-column-max\) \+ 2\.5rem\)/,
  );
  expect(css).toMatch(
    /\.harness-composer-shell\s*\{[^}]*max-width:\s*var\(--chat-column-max\)/,
  );
  // Bubble caps ship with the column; no leftover hard-coded 640px cap.
  expect(css).not.toMatch(/max-width:\s*min\(640px/);
});

it("lets the title band pass clicks through to the floating controls", () => {
  expect(css).toMatch(/\.assistant-surface-title\s*\{[^}]*pointer-events:\s*none/);
  expect(css).toMatch(/\.assistant-surface-title > \*\s*\{[^}]*pointer-events:\s*auto/);
});
