// @vitest-environment jsdom
import { TextMessagePartProvider } from "@assistant-ui/react";
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import { MarkdownText } from "../src/components/markdown-text";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement | undefined;
let root: ReturnType<typeof createRoot> | undefined;

afterEach(() => {
  if (root) act(() => root!.unmount());
  host?.remove();
  host = undefined;
  root = undefined;
});

async function render(markdown: string) {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      <TextMessagePartProvider text={markdown} isRunning={false}>
        <MarkdownText />
      </TextMessagePartProvider>,
    );
    await new Promise((resolve) => setTimeout(resolve, 30));
  });
  return host;
}

async function click(element: Element | null | undefined) {
  expect(element).toBeTruthy();
  await act(async () => { (element as HTMLElement).click(); await new Promise((resolve) => setTimeout(resolve, 10)); });
}

const longList = Array.from({ length: 30 }, (_, index) => `${index + 1}. 产出文件 ${index + 1}.csv`).join("\n");

it("folds a long list, keeps the closing prose, and unfolds on request", async () => {
  const container = await render(`${longList}\n\n以上是本次全部产出。`);

  expect(container.querySelectorAll("li")).toHaveLength(8);
  expect(container.textContent).toContain("共 30 项");
  expect(container.textContent).toContain("展开全部（30 项）");
  // The paragraphs after the list stay readable, which is the point of folding.
  expect(container.textContent).toContain("以上是本次全部产出。");

  await click(container.querySelector(".aui-md-clamp button"));
  expect(container.querySelectorAll("li")).toHaveLength(30);
  expect(container.textContent).toContain("收起");
});

it("leaves a list that fits alone", async () => {
  const container = await render("1. 只有一个\n2. 两个\n\n正常段落。");

  expect(container.querySelectorAll("li")).toHaveLength(2);
  expect(container.querySelector(".aui-md-clamp")).toBeNull();
});

it("folds a long table body but keeps its header", async () => {
  const rows = Array.from({ length: 25 }, (_, index) => `| 文件 ${index + 1} | ${index + 1} KB |`).join("\n");
  const container = await render(`| 名称 | 大小 |\n| --- | --- |\n${rows}\n`);

  expect(container.querySelectorAll("tbody tr")).toHaveLength(8);
  expect(container.querySelectorAll("thead th")).toHaveLength(2);
  expect(container.textContent).toContain("共 25 行");

  await click(container.querySelector(".aui-md-clamp button"));
  expect(container.querySelectorAll("tbody tr")).toHaveLength(25);
});
