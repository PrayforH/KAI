import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HandoverInlineRows, shouldShowHandoverRows } from "../src/components/handover-row";
import { emitHandover } from "./helpers/handover";
import { handoverStore, statusLead } from "../src/lib/handover-store";

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
    expect(html).toContain("已发送给");
    expect(html).toContain("「行业快报监控」");
    expect(html).toContain('href="/assistant?bot=specialist-1"');
    // 行为断言走源码（SSR 不渲染 onClick）。
    const rowSource = readFileSync(join(process.cwd(), "src/components/handover-row.tsx"), "utf8");
    expect(rowSource).toContain("window.location.assign");
    handoverStore.subscribe(() => {})(); // noop to appease linter on import side effect
  });

  it("is scroll content inside the main assistant message, never a sticky top bar", () => {
    // Each receipt stays inside its originating assistant message.
    expect(agentSource).toContain("<HandoverInlineRows messageId={messageId}");
    expect(agentSource).not.toContain("HandoverStrip");
    const css = readFileSync(join(process.cwd(), "src/app/web-codex.css"), "utf8");
    expect(css).not.toMatch(/\.handover-inline[^-][^}]*position:\s*(sticky|fixed)/);
  });

  it("only mounts in the main assistant conversation, not task or bot threads", () => {
    expect(shouldShowHandoverRows(true, true, false)).toBe(true);
    // /assistant 下打开旧任务：入口仍为 true，但 data-surface 已是 task。
    expect(shouldShowHandoverRows(true, false, false)).toBe(false);
    // 子助理自己的会话不承接主助理的转交回执。
    expect(shouldShowHandoverRows(true, true, true)).toBe(false);
    // 普通任务入口（非 /assistant）永远不显示。
    expect(shouldShowHandoverRows(false, false, false)).toBe(false);
  });

  it("maps statuses to Chinese leads and dedupes per bot", () => {
    expect(statusLead("sent")).toBe("已发送给");
    expect(statusLead("pending_input")).toBe("待补充 ·");
    emitHandover({ specialist_id: "s2", specialist_name: "B", status: "sent" });
    emitHandover({ specialist_id: "s2", specialist_name: "B2", status: "applied" });
    const names = handoverStore.snapshot().filter((n) => n.specialistId === "s2");
    expect(names).toHaveLength(1);
    expect(names[0]?.specialistName).toBe("B2");
  });
});
