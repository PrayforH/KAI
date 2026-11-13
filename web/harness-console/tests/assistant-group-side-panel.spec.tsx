// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupSidePanel } from "../src/components/assistant-group-side-panel";
import type {
  AssistantGroup,
  AssistantGroupLibrary,
  GroupMessage,
} from "../src/lib/assistant-client";
import type { StudioKnowledgeBase } from "../src/lib/studio-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const group: AssistantGroup = {
  groupId: "g1",
  name: "产品评审群",
  creatorUserId: "u1",
  primaryPresent: true,
  replyAs: null,
  lastReadAt: null,
  unreadCount: 0,
  members: [],
  createdAt: "2026-11-01T00:00:00Z",
  updatedAt: "2026-11-01T00:00:00Z",
};

function message(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    messageId: "m1",
    groupId: "g1",
    author: "primary",
    authorName: "主助理",
    specialistId: null,
    text: "已完成评审",
    runId: "r1",
    replyToMessageId: null,
    reactions: [],
    status: "completed",
    createdAt: "2026-11-13T05:00:00Z",
    ...overrides,
  };
}

const library: AssistantGroupLibrary = {
  documents: [
    {
      documentId: "d1",
      name: "需求.docx",
      contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
      sizeBytes: 2048,
      uploadedBy: "u1",
      uploadedByName: "我",
      createdAt: "2026-11-13T05:00:00Z",
      downloadUrl: "/download/d1",
    },
  ],
  knowledgeBases: [
    { reference: "product-kb", name: "产品知识库", type: "rag", engine: "weknora", sourceCount: 3 },
  ],
};

const availableKnowledgeBases = [
  {
    tenantId: "t1",
    reference: "other-kb",
    displayName: "另一个知识库",
    description: "",
    sourceReferences: [],
    kbType: "rag",
    engine: "weknora",
    engineRef: "e1",
    documentCount: 1,
    revision: 1,
    createdBy: "u1",
    updatedBy: "u1",
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  },
] as StudioKnowledgeBase[];

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

function render(element: ReactElement) {
  return act(async () => root!.render(element));
}

function baseProps() {
  return {
    group,
    messages: [message()],
    currentUserId: "u1",
    isCreator: false,
    tab: "activity" as const,
    onTabChange: vi.fn(),
    onClose: vi.fn(),
    library,
    libraryLoading: false,
    libraryError: null,
    availableKnowledgeBases,
    libraryBusy: false,
    onUpload: vi.fn(),
    onDeleteDocument: vi.fn(),
    onBind: vi.fn(),
    onUnbind: vi.fn(),
  };
}

describe("AssistantGroupSidePanel", () => {
  it("shows group activity with the three tabs", async () => {
    await render(<AssistantGroupSidePanel {...baseProps()} />);
    expect(host.querySelectorAll('[role="tab"]')).toHaveLength(3);
    expect(host.textContent).toContain("主助理");
    expect(host.textContent).toContain("已完成评审");
  });

  it("lists runs in the tasks tab", async () => {
    await render(<AssistantGroupSidePanel {...baseProps()} tab="tasks" />);
    expect(host.textContent).toContain("已完成");
    expect(host.textContent).toContain("已完成评审");
  });

  it("uploads a file from the library tab", async () => {
    const props = baseProps();
    await render(<AssistantGroupSidePanel {...props} tab="library" />);
    const input = host.querySelector<HTMLInputElement>('input[type="file"]')!;
    const file = new File(["hello"], "readme.txt", { type: "text/plain" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    await act(async () => input.dispatchEvent(new Event("change", { bubbles: true })));
    expect(props.onUpload).toHaveBeenCalledWith(file);
  });

  it("deletes a document only after a second click", async () => {
    const props = baseProps();
    await render(<AssistantGroupSidePanel {...props} tab="library" />);
    const remove = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "删除")!;
    await act(async () => remove.click());
    expect(props.onDeleteDocument).not.toHaveBeenCalled();
    const confirm = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认删除？")!;
    await act(async () => confirm.click());
    expect(props.onDeleteDocument).toHaveBeenCalledWith(library.documents[0]);
  });

  it("binds and unbinds knowledge bases", async () => {
    const props = baseProps();
    await render(<AssistantGroupSidePanel {...props} tab="library" />);
    const unbind = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "解绑")!;
    await act(async () => unbind.click());
    expect(props.onUnbind).toHaveBeenCalledWith(library.knowledgeBases[0]);

    const select = host.querySelector<HTMLSelectElement>('[aria-label="选择要绑定的知识库"]')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(select, "other-kb");
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
    const bind = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "绑定")!;
    await act(async () => bind.click());
    expect(props.onBind).toHaveBeenCalledWith("other-kb");
  });

  it("hides the delete action for files uploaded by someone else", async () => {
    await render(
      <AssistantGroupSidePanel
        {...baseProps()}
        tab="library"
        currentUserId="u2"
        isCreator={false}
      />,
    );
    expect([...host.querySelectorAll<HTMLButtonElement>("button")]
      .some((button) => button.textContent === "删除")).toBe(false);
  });

  it("shows empty states when the library is empty", async () => {
    await render(
      <AssistantGroupSidePanel
        {...baseProps()}
        tab="library"
        library={{ documents: [], knowledgeBases: [] }}
        availableKnowledgeBases={[]}
      />,
    );
    expect(host.textContent).toContain("群资料库还没有文件");
    expect(host.textContent).toContain("还没有绑定知识库");
  });
});
