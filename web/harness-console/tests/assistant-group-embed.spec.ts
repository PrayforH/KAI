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
const sharedActions = readFileSync(
  join(process.cwd(), "src/components/message-actions.tsx"),
  "utf8",
);
const client = readFileSync(join(process.cwd(), "src/lib/assistant-client.ts"), "utf8");
const sidePanel = readFileSync(
  join(process.cwd(), "src/components/assistant-group-side-panel.tsx"),
  "utf8",
);

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
      "mentionTargets",
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

  it("keeps group info behind the title and members in their own dialog", () => {
    // 旧的 ⋯ 群信息面板整块删除；点标题带群名打开只读群资料，
    // 成员管理仍由标题带的头像簇拉起，两者不再是同一个面板。
    expect(groupChat).toContain("<AssistantGroupInfoDialog");
    expect(groupChat).toContain('aria-label={`查看 ${selected.name} 群资料`}');
    expect(groupChat).not.toContain("settingsOpen");
    expect(groupChat).not.toContain("styles.toolbarInner");
    expect(groupChat).toContain("<AssistantGroupMembersDialog");
    expect(groupChat).toContain("setMembersOpen(true)");
    // 右侧只剩「动态 / 任务 / 资料库」；聊天记录改成标题带的独立按钮。
    expect(groupChat).toContain("<AssistantGroupSidePanel");
    expect(groupChat).toContain('aria-label={sideOpen ? "关闭群右侧栏" : "打开群右侧栏"}');
    expect(groupChat).toContain('sideOpen ? setSideOpen(false) : openSidePanel("activity")');
    expect(groupChat).toContain("title=\"聊天记录\"");
    expect(sidePanel).not.toContain("群信息");
    expect(sidePanel).not.toContain('key: "history"');
  });

  it("renders messages through the shared row and composer components", () => {
    expect(groupChat).toContain("<AssistantGroupMessage");
    expect(groupChat).toContain("<AssistantGroupComposer");
    expect(groupChat).toContain("onReply={handleQuote}");
    // 悬停操作与单助理共用同一套操作条，群会话不再各写一份表情/复制/删除。
    expect(messageRow).toContain('from "./message-actions"');
    expect(messageRow).toContain("<MessageActions");
    expect(sharedActions).toContain('aria-label="引用这条消息"');
    expect(sharedActions).toContain('aria-label="复制这条消息"');
    expect(markdown).toContain("remarkMentions");
    expect(markdown).toContain("remarkGfm");
  });

  it("sends the quote id and keeps the draft when sending fails", () => {
    expect(groupChat).toContain("quoted?.messageId ?? null");
    // 成功路径先 await 发送，再清草稿和引用；失败时两者都保留。
    const send = groupChat.slice(
      groupChat.indexOf("const handleSend"),
      groupChat.indexOf("const handleReaction"),
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
    // 不再有 @ 按钮：输入 @ 直接弹成员选择，与单助理共用同一套选择器。
    expect(composer).toContain('testId="group-mention-menu"');
    expect(composer).toContain("mentionTrigger");
    // 中文输入法选词 Enter 不能发消息。
    expect(composer).toContain("isComposing");
    expect(composer).toContain("keyCode === 229");
    // 附件/知识库入口与草稿、附件随消息发送。
    expect(composer).toContain('aria-label="添加附件或知识库"');
    expect(groupChat).toContain("attachmentIds");
    expect(groupChat).toContain("loadGroupComposerDraft");
    // 底部键盘提示已按用户要求移除。
    expect(groupChat).not.toContain("Shift+Enter 换行 · 消息与回复只保存在本群");
    expect(composer).not.toContain("group-composer-hint");
  });

  it("supports group management without single-message deletion", () => {
    expect(client).toContain("export async function updateAssistantGroup");
    // 只有解散群，消息级删除已统一取消。
    expect(client).toContain("export async function deleteAssistantGroup");
    expect(client).not.toContain("deleteAssistantGroupMessage");
    expect(client).toContain("library/documents");
    expect(sharedActions).not.toContain('"删除这条消息"');
    expect(sharedActions).not.toContain("onDelete");
    expect(messageRow).not.toContain("canDelete");
    expect(groupChat).not.toContain("deleteAssistantGroupMessage");
    expect(groupChat).toContain("onDissolved");
  });

  it("accepts an optional reply snapshot from the groups API", () => {
    expect(client).toContain("export type GroupMessageReply");
    expect(client).toContain("replyTo?: GroupMessageReply");
    expect(client).toContain("replyToMessageId?: string | null");
    expect(client).toContain("replyToMessageId: replyToMessageId ?? undefined");
  });

  it("opens the members dialog from the avatar cluster in the group title strip", () => {
    // 助理面无顶栏，所以头像簇放在群聊自己的固定标题带里，而不是 page 的 header。
    expect(groupChat).toContain("group-member-faces");
    expect(groupChat).toContain("setMembersOpen(true)");
    expect(page).not.toContain("group-member-faces");
    expect(page).not.toContain("kai:group-members-open");
  });
});
