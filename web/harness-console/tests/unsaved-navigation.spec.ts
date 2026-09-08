import { describe, expect, it } from "vitest";
import {
  createUnsavedHistoryGuard,
  guardedNavigationDestination,
  navigationLabel,
  type HistoryAdapter,
} from "../src/lib/unsaved-navigation";

const currentHref = "https://studio.example.com/studio/agents?draft=one";

class FakeHistory implements HistoryAdapter {
  entries: Array<{ state: unknown; url: string }>;
  index: number;

  constructor() {
    this.entries = [
      { state: { page: "capabilities" }, url: "/studio/capabilities" },
      { state: { page: "agents", tree: "next-router" }, url: "/studio/agents" },
    ];
    this.index = 1;
  }

  get state() {
    return this.entries[this.index]?.state ?? null;
  }

  back() {
    this.index = Math.max(0, this.index - 1);
  }

  forward() {
    this.index = Math.min(this.entries.length - 1, this.index + 1);
  }

  pushState(state: unknown, _unused: string, url?: string | URL | null) {
    this.entries = this.entries.slice(0, this.index + 1);
    this.entries.push({ state, url: String(url ?? this.entries[this.index]?.url ?? "") });
    this.index += 1;
  }

  replaceState(state: unknown, _unused: string, url?: string | URL | null) {
    this.entries[this.index] = {
      state,
      url: String(url ?? this.entries[this.index]?.url ?? ""),
    };
  }
}

describe("Studio unsaved navigation", () => {
  it("guards current-tab internal and external destinations", () => {
    expect(guardedNavigationDestination({
      currentHref,
      targetHref: "/studio/capabilities",
      button: 0,
    })?.href).toBe("https://studio.example.com/studio/capabilities");
    expect(guardedNavigationDestination({
      currentHref,
      targetHref: "https://docs.example.com/guide",
      button: 0,
    })?.href).toBe("https://docs.example.com/guide");
  });

  it("does not block new tabs, downloads, modified clicks or hash movement", () => {
    const ignored = [
      { targetHref: "/", button: 1 },
      { targetHref: "/", button: 0, metaKey: true },
      { targetHref: "/", button: 0, target: "_blank" },
      { targetHref: "/export", button: 0, download: true },
      { targetHref: `${currentHref}#prompt`, button: 0 },
      { targetHref: "mailto:owner@example.com", button: 0 },
    ];
    for (const intent of ignored) {
      expect(guardedNavigationDestination({ currentHref, ...intent })).toBeNull();
    }
  });

  it("uses compact destination copy in the confirmation", () => {
    const destination = new URL("https://studio.example.com/studio/knowledge");
    expect(navigationLabel("  知识库   连接  ", destination)).toBe("知识库 连接");
    expect(navigationLabel(null, destination)).toBe("/studio/knowledge");
  });

  it("turns the first browser Back into a prompt and restores the guarded entry", () => {
    const history = new FakeHistory();
    const guard = createUnsavedHistoryGuard(history, "guard-1");
    guard.activate(currentHref);

    expect(history.entries).toHaveLength(3);
    expect(history.state).toMatchObject({ page: "agents", tree: "next-router" });

    history.back();
    expect(guard.handlePopState(history.state)).toBe("prompt");
    expect(history.index).toBe(2);
    expect(guard.handlePopState(history.state)).toBe("handled");
    expect(guard.isActive()).toBe(true);
  });

  it("removes the guard before continuing a confirmed navigation", () => {
    const history = new FakeHistory();
    const guard = createUnsavedHistoryGuard(history, "guard-2");
    let continued = false;
    guard.activate(currentHref);
    guard.deactivate(() => {
      continued = true;
    });

    expect(history.index).toBe(1);
    expect(guard.handlePopState(history.state)).toBe("handled");
    expect(guard.isActive()).toBe(false);
    expect(continued).toBe(true);
    expect(history.state).toEqual({ page: "agents", tree: "next-router" });
  });

  it("finishes a pending removal when save completes during history restoration", () => {
    const history = new FakeHistory();
    const guard = createUnsavedHistoryGuard(history, "guard-3");
    let continued = false;
    guard.activate(currentHref);

    history.back();
    expect(guard.handlePopState(history.state)).toBe("prompt");
    guard.deactivate(() => {
      continued = true;
    });
    expect(guard.handlePopState(history.state)).toBe("handled");
    expect(history.index).toBe(1);
    expect(guard.handlePopState(history.state)).toBe("handled");
    expect(continued).toBe(true);
    expect(guard.isActive()).toBe(false);
  });
});
