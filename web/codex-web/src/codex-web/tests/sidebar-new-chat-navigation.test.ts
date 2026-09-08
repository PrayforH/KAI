import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("../../components/layout/ChatListPanel.tsx", import.meta.url),
  "utf8",
);
const headerSource = readFileSync(
  new URL("../../components/layout/ProjectGroupHeader.tsx", import.meta.url),
  "utf8",
);

describe("侧栏新对话导航", () => {
  it("项目新对话使用 replace 避免重复历史导航", () => {
    expect(source).toContain("router.replace(createNewChatHref());");
    expect(source).not.toContain("router.push(createNewChatHref());");
  });

  it("项目新对话先同步工作目录再导航", () => {
    const handlerStart = source.indexOf("const handleCreateSessionInProject");
    const handler = source.slice(handlerStart, source.indexOf("\n  };", handlerStart));
    expect(handler).toContain("localStorage.setItem('codepilot:last-working-directory', workingDirectory)");
    expect(handler).toContain("project-directory-changed");
    expect(handler).toContain("window.matchMedia(COMPACT_VIEWPORT_QUERY).matches");
    expect(handler).toContain("setChatListOpen(false)");
    expect(handler).toContain("router.replace(createNewChatHref())");
    expect(handler.indexOf("setChatListOpen(false)")).toBeLessThan(handler.indexOf("router.replace(createNewChatHref())"));
  });

  it("项目铅笔按钮不会触发表单提交", () => {
    const actionStart = headerSource.indexOf("{/* New chat button");
    const action = headerSource.slice(actionStart, headerSource.indexOf("</Button>", actionStart));
    expect(action).toContain('type="button"');
  });

  it("移动端项目操作区不依赖 hover 即可点击", () => {
    expect(headerSource).toContain('"opacity-100 md:opacity-0 md:pointer-events-none"');
    expect(headerSource).not.toContain('"opacity-0 pointer-events-none"');
  });
});
