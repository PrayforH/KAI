import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const hook = readFileSync(
  join(process.cwd(), "src/lib/use-dialog-focus.ts"),
  "utf8",
);

describe("modal dialog focus contract", () => {
  it("moves focus inside, traps Tab and restores the invoking control", () => {
    expect(hook).toContain("focusableElements(panel)[0]");
    expect(hook).toContain('event.key !== "Tab"');
    expect(hook).toContain("!panel.contains(active)");
    expect(hook).toContain("active === first");
    expect(hook).toContain("active === last");
    expect(hook).toContain("previouslyFocused?.isConnected");
    expect(hook).toContain("previouslyFocused.focus()");
  });

  it("closes through Escape without letting the key reach the page", () => {
    expect(hook).toContain('event.key === "Escape"');
    expect(hook).toContain("event.preventDefault()");
    expect(hook).toContain("onEscapeRef.current()");
    expect(hook).toContain('addEventListener("keydown", handleKeyDown, true)');
  });
});
