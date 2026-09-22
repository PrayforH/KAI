// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";

import { KnowledgeBaseList } from "../src/components/task-knowledge-context";
import type { StudioKnowledgeBase } from "../src/lib/studio-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const bases = [
  { reference: "cases", displayName: "案例库", engine: "weknora", kbType: "hybrid" },
  { reference: "policy", displayName: "政策库", engine: "weknora", kbType: "rag" },
] as StudioKnowledgeBase[];

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function Bound({ initial = [] as string[] }) {
  const [selected, setSelected] = useState<string[]>(initial);
  return (
    <KnowledgeBaseList
      available={bases}
      selected={selected}
      loading={false}
      error=""
      onToggle={(reference) =>
        setSelected((current) =>
          current.includes(reference)
            ? current.filter((item) => item !== reference)
            : [...current, reference],
        )
      }
      onClear={() => setSelected([])}
      onRetry={() => {}}
    />
  );
}

it("ticks and clears whatever the caller binds — a thread or an agent draft", () => {
  act(() => root.render(<Bound initial={["cases"]} />));

  const options = [...host.querySelectorAll<HTMLButtonElement>(".task-knowledge-option")];
  expect(host.querySelector(".task-knowledge-menu-head")?.textContent).toBe("知识库 · 已选 1 个");
  expect(options[0]!.getAttribute("aria-pressed")).toBe("true");
  expect(options[1]!.getAttribute("aria-pressed")).toBe("false");

  act(() => options[1]!.click());
  expect(host.querySelector(".task-knowledge-menu-head")?.textContent).toBe("知识库 · 已选 2 个");
  expect(options[1]!.getAttribute("aria-pressed")).toBe("true");

  act(() => host.querySelector<HTMLButtonElement>(".task-knowledge-clear")!.click());
  expect(host.querySelector(".task-knowledge-menu-head")?.textContent).toBe("知识库 · 已选 0 个");
  // Nothing to clear means nothing to press: the button must reflect the binding.
  expect(host.querySelector<HTMLButtonElement>(".task-knowledge-clear")!.disabled).toBe(true);
});

it("separates the empty catalog from an empty search", () => {
  act(() =>
    root.render(
      <KnowledgeBaseList
        available={[]}
        selected={[]}
        loading={false}
        error=""
        onToggle={() => {}}
        onClear={() => {}}
        onRetry={() => {}}
      />,
    ),
  );
  expect(host.querySelector(".task-knowledge-empty")?.textContent).toBe("暂无可用知识库");

  const search = host.querySelector<HTMLInputElement>(".task-knowledge-search")!;
  // React only sees the change when the native setter runs, then the event fires.
  const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setValue.call(search, "案例");
    search.dispatchEvent(new Event("input", { bubbles: true }));
  });
  expect((host.querySelector(".task-knowledge-empty")?.textContent ?? "")).toContain("没有匹配");
});

it("offers a retry when the catalog failed to load", () => {
  let retried = 0;
  act(() =>
    root.render(
      <KnowledgeBaseList
        available={[]}
        selected={[]}
        loading={false}
        error="知识库加载失败，请重试"
        onToggle={() => {}}
        onClear={() => {}}
        onRetry={() => { retried += 1; }}
      />,
    ),
  );

  const alert = host.querySelector('[role="alert"]');
  expect(alert?.textContent).toContain("知识库加载失败");
  act(() => [...(alert?.querySelectorAll("button") ?? [])][0]!.click());
  expect(retried).toBe(1);
});
