import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
const botRail = readFileSync(join(process.cwd(), "src/components/bot-rail.tsx"), "utf8");
const taskSidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);
const groupChat = readFileSync(
  join(process.cwd(), "src/components/assistant-group-chat.tsx"),
  "utf8",
);
const groupsRoute = readFileSync(
  join(process.cwd(), "src/app/assistant/groups/page.tsx"),
  "utf8",
);
const composer = readFileSync(
  join(process.cwd(), "src/components/assistant-group-composer.tsx"),
  "utf8",
);
const messageRow = readFileSync(
  join(process.cwd(), "src/components/assistant-group-message.tsx"),
  "utf8",
);
const markdown = readFileSync(
  join(process.cwd(), "src/components/assistant-group-markdown.tsx"),
  "utf8",
);
const client = readFileSync(join(process.cwd(), "src/lib/assistant-client.ts"), "utf8");

describe("group chats live in the main assistant surface", () => {
  it("opens a group through ?group= inside the main chat stage", () => {
    expect(page).toContain('searchParams.get("group")');
    expect(page).toContain("<AssistantGroupChat");
    expect(page).toContain("groupParam !== null");
    expect(page).not.toContain('href="/assistant/groups"');
  });

  it("routes rail clicks and newly created groups to the main page", () => {
    expect(botRail).toContain("/assistant?group=");
    expect(botRail).not.toContain("/assistant/groups?group=");
    expect(botRail).toContain("data-active={activeGroupId === group.groupId");
    expect(taskSidebar).toContain("activeGroupId={activeGroupId}");
  });

  it("keeps the old groups URL as a redirect only", () => {
    expect(groupsRoute).toContain("redirect(");
    expect(groupsRoute).toContain("/assistant?group=");
    expect(groupsRoute).not.toContain("AssistantGroupsPage");
  });

  it("keeps group capabilities in the embedded conversation", () => {
    for (const capability of [
      "postAssistantGroupMessage",
      "toggleAssistantGroupReaction",
      "markAssistantGroupRead",
      "addAssistantGroupMember",
      "removeAssistantGroupMember",
      "setAssistantGroupReplyAs",
      "insertMention",
    ]) {
      expect(groupChat).toContain(capability);
    }
  });

  it("reuses the assistant conversation look without forwarding entries", () => {
    // 输入框占位与主助理会话同一套：给 {群名} 发消息。
    expect(groupChat).toContain("`给 ${selected.name} 发消息`");
    // 群聊是共享会话，不做单聊那种转发/转交。
    expect(groupChat).not.toContain("消息来自");
    expect(groupChat).not.toContain("已发消息给");
    expect(groupChat).not.toContain("转发");
    expect(groupChat).not.toContain("转交");
    // 对话区分隔与未读提示。
    expect(groupChat).toContain("styles.stamp");
    expect(groupChat).toContain("styles.unreadLine");
    expect(groupChat).toContain("条新消息");
  });

  it("keeps the roster behind the group info toggle", () => {
    // 花名册/邀请/接话人默认收起，对话区上方不再有一条工具条。
    expect(groupChat).toContain('aria-label="群信息"');
    expect(groupChat).toContain("<AssistantGroupInfoPanel");
    expect(groupChat).toContain("aria-expanded={settingsOpen}");
    expect(groupChat).not.toContain("styles.toolbarInner");
    // 右侧「动态 / 任务 / 资料库」侧栏。
    expect(groupChat).toContain("<AssistantGroupSidePanel");
    expect(groupChat).toContain('aria-label="打开群右侧栏"');
  });

  it("renders messages through the shared row and composer components", () => {
    expect(groupChat).toContain("<AssistantGroupMessage");
    expect(groupChat).toContain("<AssistantGroupComposer");
    expect(groupChat).toContain("onReply={handleQuote}");
    // 悬停操作放在消息行里，不再由群会话拼表情按钮。
    expect(messageRow).toContain('aria-label="引用这条消息"');
    expect(messageRow).toContain('aria-label="复制这条消息"');
    expect(markdown).toContain("remarkMentions");
    expect(markdown).toContain("remarkGfm");
  });

  it("sends the quote id and keeps the draft when sending fails", () => {
    expect(groupChat).toContain("quoted?.messageId ?? null");
    // 成功路径先 await 发送，再清草稿和引用；失败时两者都保留。
    const send = groupChat.slice(
      groupChat.indexOf("const handleSend"),
      groupChat.indexOf("const insertMention"),
    );
    expect(send.indexOf("await postAssistantGroupMessage")).toBeLessThan(
      send.indexOf("setQuoted(null)"),
    );
    expect(send).toContain('setError(reason instanceof Error ? reason.message : "发送失败")');
  });

  it("has a one-line composer with a quote bar and a capped textarea", () => {
    expect(composer).toContain("rows={1}");
    // 5 行封顶：22 × 5 + 上下内边距 20 = 130px。
    expect(composer).toContain("MAX_HEIGHT = 130");
    expect(composer).toContain("event.shiftKey");
    expect(composer).toContain('aria-label="取消引用"');
    expect(composer).toContain('aria-label="提到群成员"');
    // 中文输入法选词 Enter 不能发消息。
    expect(composer).toContain("isComposing");
    expect(composer).toContain("keyCode === 229");
    // 附件/知识库入口与草稿、附件随消息发送。
    expect(composer).toContain('aria-label="添加附件或知识库"');
    expect(groupChat).toContain("attachmentIds");
    expect(groupChat).toContain("loadGroupComposerDraft");
    // 提示在卡片外，不再撑高单行外框。
    expect(composer).toContain('data-testid="group-composer-hint"');
  });

  it("supports group management and message deletion", () => {
    expect(client).toContain("export async function updateAssistantGroup");
    expect(client).toContain("export async function deleteAssistantGroup");
    expect(client).toContain("export async function deleteAssistantGroupMessage");
    expect(client).toContain("library/documents");
    expect(messageRow).toContain('"删除这条消息"');
    expect(messageRow).toContain("canDelete");
    expect(groupChat).toContain("onDissolved");
  });

  it("accepts an optional reply snapshot from the groups API", () => {
    expect(client).toContain("export type GroupMessageReply");
    expect(client).toContain("replyTo?: GroupMessageReply");
    expect(client).toContain("replyToMessageId?: string | null");
    expect(client).toContain("replyToMessageId: replyToMessageId ?? undefined");
  });

  it("shows the member avatar cluster in the group header", () => {
    expect(page).toContain("onGroupMembers={setGroupMembers}");
    expect(page).toContain("group-member-faces");
    expect(page).toContain("kai:group-settings-open");
    expect(groupChat).toContain('window.addEventListener("kai:group-settings-open"');
  });
});
