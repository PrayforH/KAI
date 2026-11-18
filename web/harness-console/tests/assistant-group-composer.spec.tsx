// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupComposer } from "../src/components/assistant-group-composer";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

const members = [
  { memberId: "primary", name: "主助理" },
  { memberId: "kernel", name: "Agent 内核" },
];

type HarnessProps = {
  initial?: string;
  quoted?: { authorName: string; text: string } | null;
  sending?: boolean;
  onSend?: () => void;
  onClearQuote?: () => void;
};

function Harness({
  initial = "",
  quoted = null,
  sending = false,
  onSend = vi.fn(),
  onClearQuote = vi.fn(),
}: HarnessProps) {
  const [value, setValue] = useState(initial);
  return (
    <AssistantGroupComposer
      value={value}
      onChange={setValue}
      onSend={onSend}
      sending={sending}
      placeholder="给 演示群 发消息"
      members={members}
      quoted={quoted}
      onClearQuote={onClearQuote}
    />
  );
}

function type(value: string) {
  const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )!.set!;
  return act(async () => {
    setter.call(input, value);
    input.setSelectionRange(value.length, value.length);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("AssistantGroupComposer", () => {
  it("starts as a single-line input with a disabled send button", async () => {
    await act(async () => root!.render(<Harness />));
    const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
    expect(input.rows).toBe(1);
    expect(input.placeholder).toBe("给 演示群 发消息");
    expect(host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!.disabled).toBe(true);
  });

  it("keeps + leftmost and the send button rightmost after the input", async () => {
    await act(async () => root!.render(<Harness />));
    const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
    const send = host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!;
    // 发送在输入框之后（最右），不再是紧跟 + 的第二个按钮。
    expect(input.compareDocumentPosition(send) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const buttons = [...input.parentElement!.querySelectorAll("button")];
    expect(buttons[0]!.getAttribute("aria-label")).toContain("添加附件或知识库");
    expect(buttons.at(-1)!.getAttribute("aria-label")).toBe("发送");
    // 语音紧挨发送左边：没有麦克风权限时按钮不渲染，但容器仍在同一位置。
    expect(input.parentElement!.lastElementChild!.getAttribute("aria-label")).toBe("发送");
  });

  it("grows to five lines and then scrolls internally at 130px", async () => {
    await act(async () => root!.render(<Harness />));
    const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
    // jsdom 不做排版，用可控的 scrollHeight 模拟内容实际高度。
    let scrollHeight = 0;
    Object.defineProperty(input, "scrollHeight", {
      configurable: true,
      get: () => scrollHeight,
    });

    // 5 行 = 22 × 5 + 上下内边距 20 = 130px，正好到上限，仍不滚动。
    scrollHeight = 130;
    await type("第五行");
    expect(input.style.height).toBe("130px");
    expect(input.style.overflowY).toBe("hidden");

    // 超过 5 行后封顶在 130px，改为内部滚动，不再把外框撑高。
    scrollHeight = 240;
    await type("第八行\n第八行\n第八行");
    expect(input.style.height).toBe("130px");
    expect(input.style.overflowY).toBe("auto");
  });

  it("enables send once there is text and calls onSend on Enter", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("你好");
    const send = host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!;
    expect(send.disabled).toBe(false);
    await act(async () => {
      host
        .querySelector("textarea")!
        .dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("keeps Shift+Enter for a new line", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("第一行");
    await act(async () => {
      host
        .querySelector("textarea")!
        .dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }),
        );
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows the one-line quote bar and clears it", async () => {
    const onClearQuote = vi.fn();
    await act(async () =>
      root!.render(
        <Harness
          quoted={{ authorName: "Agent 内核", text: "上一句被引用的内容" }}
          onClearQuote={onClearQuote}
        />,
      ),
    );
    expect(host.textContent).toContain("Agent 内核");
    expect(host.textContent).toContain("上一句被引用的内容");
    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label="取消引用"]')!.click(),
    );
    expect(onClearQuote).toHaveBeenCalledTimes(1);
  });

  it("has no @ button and opens the picker by typing @", async () => {
    await act(async () => root!.render(<Harness />));
    expect(host.querySelector('[aria-label="提到群成员"]')).toBeNull();
    await type("请 @");
    const menu = host.querySelector('[data-testid="group-mention-menu"]')!;
    expect(menu).not.toBeNull();
    const options = menu.querySelectorAll('[role="option"]');
    expect(options).toHaveLength(2);
    expect(options[0]!.textContent).toContain("主助理");
    expect(options[1]!.textContent).toContain("Agent 内核");
    await act(async () => (options[1] as HTMLElement).click());
    const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
    // 替换掉光标前的 @ 词，而不是追加到末尾。
    expect(input.value).toBe("请 @Agent 内核 ");
  });

  it("filters the picker by the text typed after @", async () => {
    await act(async () => root!.render(<Harness />));
    await type("@内");
    const options = host.querySelectorAll('[role="option"]');
    expect(options).toHaveLength(1);
    expect(options[0]!.textContent).toContain("Agent 内核");
  });

  it("chooses the highlighted member with Enter instead of sending", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("@");
    const input = host.querySelector("textarea")!;
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
    });
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSend).not.toHaveBeenCalled();
    expect(input.value).toBe("@Agent 内核 ");
  });

  it("closes the picker with Escape, then Enter sends", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("@");
    const input = host.querySelector("textarea")!;
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    expect(host.querySelector('[data-testid="group-mention-menu"]')).toBeNull();
    await act(async () => {
      input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("does not open the picker while a Chinese IME is composing", async () => {
    await act(async () => root!.render(<Harness />));
    const input = host.querySelector("textarea")!;
    await act(async () => {
      input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    });
    await type("@");
    expect(host.querySelector('[data-testid="group-mention-menu"]')).toBeNull();
  });

  it("shows an empty state when nothing matches", async () => {
    await act(async () => root!.render(<Harness />));
    await type("@不存在");
    expect(host.querySelector('[data-testid="group-mention-menu"]')!.textContent).toContain(
      "没有匹配的成员",
    );
  });

  it("ignores Enter while a Chinese IME is composing", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("你好");
    const input = host.querySelector("textarea")!;
    await act(async () => {
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }),
      );
    });
    await act(async () => {
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", keyCode: 229, bubbles: true }),
      );
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("renders no keyboard hint under the composer", async () => {
    await act(async () => root!.render(<Harness />));
    expect(host.querySelector('[data-testid="group-composer-hint"]')).toBeNull();
  });

  it("lists group library documents in the + menu and toggles them", async () => {
    const onToggleAttachment = vi.fn();
    const onOpenLibrary = vi.fn();
    await act(async () =>
      root!.render(
        <AssistantGroupComposer
          value=""
          onChange={vi.fn()}
          onSend={vi.fn()}
          sending={false}
          placeholder="发消息"
          members={members}
          quoted={null}
          onClearQuote={vi.fn()}
          libraryDocuments={[
            {
              documentId: "d1",
              name: "方案.pdf",
              contentType: "application/pdf",
              sizeBytes: 1024,
              uploadedBy: "u1",
              uploadedByName: "我",
              createdAt: "2026-11-13T05:00:00Z",
              downloadUrl: "/download/d1",
            },
          ]}
          attachmentIds={["d1"]}
          onToggleAttachment={onToggleAttachment}
          onOpenLibrary={onOpenLibrary}
        />,
      ),
    );
    // 已选附件显示为可移除的 chip。
    expect(host.textContent).toContain("方案.pdf");
    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label="移除附件 方案.pdf"]')!.click(),
    );
    expect(onToggleAttachment).toHaveBeenCalledWith("d1");

    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label^="添加附件或知识库"]')!.click(),
    );
    const item = host.querySelector('[role="menuitemcheckbox"]')!;
    expect(item.getAttribute("aria-checked")).toBe("true");
    await act(async () => (item as HTMLButtonElement).click());
    expect(onToggleAttachment).toHaveBeenCalledTimes(2);

    // 「+」里第一项是真上传，第二项才是打开资料库。
    expect(host.textContent).toContain("上传文件");
    const openLibrary = [...host.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
      .find((button) => button.textContent === "打开资料库")!;
    await act(async () => openLibrary.click());
    expect(onOpenLibrary).toHaveBeenCalledTimes(1);
  });

  it("uploads a chosen file from the + menu and attaches the new document", async () => {
    const onUploadDocument = vi.fn(async (file: File) => ({
      documentId: "d2",
      name: file.name,
      contentType: "text/plain",
      sizeBytes: file.size,
      uploadedBy: "u1",
      uploadedByName: "我",
      createdAt: "2026-11-13T05:00:00Z",
      downloadUrl: "/download/d2",
    }));
    const onToggleAttachment = vi.fn();
    await act(async () =>
      root!.render(
        <AssistantGroupComposer
          value=""
          onChange={vi.fn()}
          onSend={vi.fn()}
          sending={false}
          placeholder="发消息"
          members={members}
          quoted={null}
          onClearQuote={vi.fn()}
          onUploadDocument={onUploadDocument}
          onToggleAttachment={onToggleAttachment}
        />,
      ),
    );
    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label^="添加附件或知识库"]')!.click(),
    );
    const file = new File(["hello"], "会议纪要.txt", { type: "text/plain" });
    const picker = host.querySelector<HTMLInputElement>('input[type="file"]')!;
    Object.defineProperty(picker, "files", { configurable: true, value: [file] });
    await act(async () => picker.dispatchEvent(new Event("change", { bubbles: true })));
    expect(onUploadDocument).toHaveBeenCalledWith(file);
    expect(onToggleAttachment).toHaveBeenCalledWith("d2");
  });

  it("does not send while a send is in flight", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness initial="你好" sending onSend={onSend} />));
    expect(host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!.disabled).toBe(true);
    await act(async () => {
      host
        .querySelector("textarea")!
        .dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("suppresses the textarea focus ring but keeps the composer focus border", () => {
    // jsdom 不做样式计算，这里守住修复的结构前提：内圈 outline 被压掉，
    // 焦点提示仍由外层胶囊的 focus-within 边框承担。
    const css = readFileSync(
      resolve(process.cwd(), "src/components/assistant-group-composer.module.css"),
      "utf8",
    );
    const inputFocusBlock =
      css.match(/\.input:focus,\s*\.input:focus-visible\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(inputFocusBlock).toContain("outline: none !important");

    const composerFocusBlock = css.match(/\.composer:focus-within\s*\{([^}]*)\}/)?.[1] ?? "";
    expect(composerFocusBlock).toContain("border-color");
  });
});

describe("AssistantGroupComposer 粘贴与拖拽上传", () => {
  const document = (file: File) => ({
    documentId: "d9",
    name: file.name,
    contentType: file.type || "application/octet-stream",
    sizeBytes: file.size,
    uploadedBy: "u1",
    uploadedByName: "我",
    createdAt: "2026-11-18T05:00:00Z",
    downloadUrl: "/download/d9",
  });

  async function renderComposer(onUploadDocument: (file: File) => Promise<ReturnType<typeof document>>) {
    const onToggleAttachment = vi.fn();
    await act(async () => root!.render(
      <AssistantGroupComposer
        value=""
        onChange={vi.fn()}
        onSend={vi.fn()}
        sending={false}
        placeholder="发消息"
        members={members}
        quoted={null}
        onClearQuote={vi.fn()}
        onUploadDocument={onUploadDocument}
        onToggleAttachment={onToggleAttachment}
      />,
    ));
    return onToggleAttachment;
  }

  async function flush() {
    await act(async () => { await new Promise((done) => setTimeout(done, 0)); });
  }

  function pasteEvent(file: File) {
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", { value: { files: [file] } });
    return event;
  }

  function dragEvent(type: string, files: File[]) {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, "dataTransfer", { value: { types: ["Files"], files } });
    return event;
  }

  it("uploads a pasted image through the same flow as 上传文件", async () => {
    const upload = vi.fn(async (file: File) => document(file));
    const onToggleAttachment = await renderComposer(upload);
    const file = new File(["png"], "粘贴的截图.png", { type: "image/png" });
    await act(async () => host.querySelector("textarea")!.dispatchEvent(pasteEvent(file)));
    await flush();
    expect(upload).toHaveBeenCalledWith(file);
    expect(onToggleAttachment).toHaveBeenCalledWith("d9");
  });

  it("accepts a dropped file and shows the drag hint while over the composer", async () => {
    const upload = vi.fn(async (file: File) => document(file));
    const onToggleAttachment = await renderComposer(upload);
    const file = new File(["pdf"], "评审.pdf", { type: "application/pdf" });
    await act(async () => host.querySelector<HTMLElement>("[data-dragging]")!.dispatchEvent(dragEvent("dragover", [file])));
    expect(host.textContent).toContain("松开即可上传到群资料库");
    expect(host.querySelector<HTMLElement>("[data-dragging]")!.dataset.dragging).toBe("true");

    await act(async () => host.querySelector<HTMLElement>("[data-dragging]")!.dispatchEvent(dragEvent("drop", [file])));
    await flush();
    expect(upload).toHaveBeenCalledWith(file);
    expect(onToggleAttachment).toHaveBeenCalledWith("d9");
    expect(host.querySelector<HTMLElement>("[data-dragging]")!.dataset.dragging).toBe("false");
  });

  it("keeps an upload failure visible instead of failing silently", async () => {
    const upload = vi.fn(async () => { throw new Error("资料库容量已满"); });
    await renderComposer(upload as unknown as (file: File) => Promise<ReturnType<typeof document>>);
    const file = new File(["png"], "大图.png", { type: "image/png" });
    await act(async () => host.querySelector("textarea")!.dispatchEvent(pasteEvent(file)));
    await flush();
    expect(host.querySelector('[role="alert"]')!.textContent).toContain("资料库容量已满");
  });
});
