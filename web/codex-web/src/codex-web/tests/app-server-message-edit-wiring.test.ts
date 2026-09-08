import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const page = readFileSync(new URL("../../app/chat/[id]/page.tsx", import.meta.url), "utf8");
const chatView = readFileSync(new URL("../../components/chat/ChatView.tsx", import.meta.url), "utf8");

describe("app-server 最近问题编辑接线", () => {
  it("历史页按被编辑 turn 边界 fork 后发送", () => {
    expect(page).toContain("appServerEditLastTurn=");
    expect(page).toContain("forkThread({ threadId, beforeTurnId })");
    expect(page).toContain("forkAndSendEditedMessage({");
    expect(page).not.toContain("appServerRollbackLastTurn=");
  });

  it("ChatView 使用可编辑消息的 turn id 请求 fork 编辑", () => {
    const messageIndex = chatView.indexOf("editableUserMessage?.turn_id");
    const editIndex = chatView.indexOf("await appServerEditLastTurn({", messageIndex);
    const boundaryIndex = chatView.indexOf("beforeTurnId: editableUserMessage.turn_id", editIndex);

    expect(messageIndex).toBeGreaterThan(-1);
    expect(editIndex).toBeGreaterThan(messageIndex);
    expect(boundaryIndex).toBeGreaterThan(editIndex);
    expect(chatView).not.toContain("await appServerRollbackLastTurn()");
  });

  it("MessageList 只接收计算出的一个可编辑消息 id", () => {
    expect(chatView).toContain("editableUserMessageId={editableUserMessageId}");
    expect(chatView).toContain("onEditUserMessage={handleEditUserMessage}");
  });
});
