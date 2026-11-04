import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HandoverInlineRows, shouldShowHandoverRows } from "../src/components/handover-row";
import { emitHandover } from "./helpers/handover";
import { handoverStore, liveMentionsForThread, mentionsForThread, statusLead } from "../src/lib/handover-store";

const agentSource = readFileSync(
  join(process.cwd(), "src/components/agent-thread.tsx"),
  "utf8",
);

describe("handover inline rows", () => {
  it("renders a centered thin line per bot with a link into its conversation", () => {
    emitHandover({
      specialist_id: "specialist-1",
      specialist_name: "行业快报监控",
      status: "sent",
    });
    const html = renderToStaticMarkup(<HandoverInlineRows messageId="assistant-run-ui-test" />);
    expect(html).toContain("已发送消息给");
    expect(html).toContain("行业快报监控");
    expect(html).toContain('href="/assistant?bot=specialist-1"');
    expect(html).toContain("<svg");
    handoverStore.subscribe(() => {})(); // noop to appease linter on import side effect
  });

  it("is scroll content inside the main assistant message, never a sticky top bar", () => {
    // Each receipt stays inside its originating assistant message.
    expect(agentSource).toContain("<HandoverInlineRows messageId={messageId}");
    expect(agentSource).not.toContain("HandoverStrip");
    const css = readFileSync(join(process.cwd(), "src/app/assistant-conversation.css"), "utf8");
    expect(css).not.toMatch(/\.handover-inline[^-][^}]*position:\s*(sticky|fixed)/);
  });

  it("only mounts in the main assistant conversation, not task or bot threads", () => {
    expect(shouldShowHandoverRows(true, false)).toBe(true);
    // /assistant 下打开旧任务：入口仍为 true，但 data-surface 已是 task。
    expect(shouldShowHandoverRows(false, false)).toBe(false);
    // 子助理自己的会话不承接主助理的转交回执。
    expect(shouldShowHandoverRows(true, true)).toBe(false);
    // 普通任务入口（非 /assistant）永远不显示。
    expect(shouldShowHandoverRows(false, false)).toBe(false);
  });

  it("maps statuses to Chinese leads and dedupes per bot", () => {
    expect(statusLead("sent")).toBe("已发送消息给");
    expect(statusLead("pending_input")).toBe("已发送消息给");
    emitHandover({ specialist_id: "s2", specialist_name: "B", status: "sent" });
    emitHandover({ specialist_id: "s2", specialist_name: "B2", status: "applied" });
    const names = handoverStore.snapshot().filter((n) => n.specialistId === "s2");
    expect(names).toHaveLength(1);
    expect(names[0]?.specialistName).toBe("B2");
  });
});

describe("@ mention stays in the sending conversation", () => {
  it("shows only this session's send, scoped to the thread it came from", () => {
    handoverStore.ingestMention({
      handoverId: "mention-1", specialistId: "specialist-9", specialistName: "编码协同",
      runId: "run-9", sourceThreadId: "thread-main", text: "帮我恢复下",
    });
    const notices = handoverStore.snapshot();
    expect(mentionsForThread(notices, "thread-main").map(item => item.text)).toContain("帮我恢复下");
    expect(liveMentionsForThread(notices, "thread-main").map(item => item.handoverId)).toEqual(["mention-1"]);
    // 换到别的助理会话不串台。
    expect(liveMentionsForThread(notices, "thread-other")).toHaveLength(0);
  });

  it("passes the composer text with the delivery receipt", () => {
    expect(agentSource).toContain("text: body,");
  });
});
