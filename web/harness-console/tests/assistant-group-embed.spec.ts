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
});
