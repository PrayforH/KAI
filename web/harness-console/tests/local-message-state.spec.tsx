// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import * as messageState from "../src/lib/local-message-state";
import { toggleLocalReaction, useLocalMessageState } from "../src/lib/local-message-state";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const KEY = "kai:assistant-message-local:thread-legacy";

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  window.localStorage.clear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
  window.localStorage.clear();
});

function Probe({ messageId }: { messageId: string }) {
  const state = useLocalMessageState("thread-legacy", messageId);
  return (
    <span
      data-testid="probe"
      data-reactions={state.reactions.join("|")}
      data-hidden={"hidden" in state ? "yes" : "no"}
    />
  );
}

describe("local assistant message state", () => {
  it("ignores a legacy hidden list and keeps stored reactions", async () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ reactions: { m1: ["👍"] }, hidden: ["m1", "m2"] }),
    );
    await act(async () => root!.render(<Probe messageId="m1" />));
    const probe = host.querySelector<HTMLElement>('[data-testid="probe"]')!;
    expect(probe.dataset.reactions).toBe("👍");
    expect(probe.dataset.hidden).toBe("no");
  });

  it("drops the legacy hidden list when a reaction is written, reactions stay", () => {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({ reactions: { m2: ["❤️"] }, hidden: ["m1"] }),
    );
    toggleLocalReaction("thread-legacy", "m1", "🎉");
    const stored = JSON.parse(window.localStorage.getItem(KEY) ?? "{}");
    expect(stored.reactions).toEqual({ m2: ["❤️"], m1: ["🎉"] });
    expect(stored.hidden).toBeUndefined();
  });

  it("no longer exposes a message-hide API", () => {
    expect("hideLocalMessage" in messageState).toBe(false);
  });
});
