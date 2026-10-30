import { readFileSync } from "node:fs";
import { join } from "node:path";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HandoverInlineRows } from "../src/components/handover-row";
import { handoverStore, statusLead } from "../src/lib/handover-store";

const agentSource = readFileSync(
  join(process.cwd(), "src/components/agent-thread.tsx"),
  "utf8",
);

describe("handover inline rows", () => {
  it("renders a centered thin line per bot with a link into its conversation", () => {
    handoverStore.ingest({
      specialist_id: "specialist-1",
      specialist_name: "行业快报监控",
      status: "sent",
    });
    const html = renderToStaticMarkup(<HandoverInlineRows />);
    expect(html).toContain("已发送给");
    expect(html).toContain("「行业快报监控」");
    expect(html).toContain('href="/assistant?bot=specialist-1"');
    // 行为断言走源码（SSR 不渲染 onClick）。
    const rowSource = readFileSync(join(process.cwd(), "src/components/handover-row.tsx"), "utf8");
    expect(rowSource).toContain("window.location.assign");
    handoverStore.subscribe(() => {})(); // noop to appease linter on import side effect
  });

  it("is scroll content inside the assistant message, never a sticky top bar", () => {
    // 挂在最后一条助手消息内（随流滚动）；旧置顶条组件不得回归。
    expect(agentSource).toContain("{personalAssistant && isLast ? <HandoverInlineRows /> : null}");
    expect(agentSource).not.toContain("HandoverStrip");
    const css = readFileSync(join(process.cwd(), "src/app/web-codex.css"), "utf8");
    expect(css).not.toMatch(/\.handover-inline[^-][^}]*position:\s*(sticky|fixed)/);
  });

  it("maps statuses to Chinese leads and dedupes per bot", () => {
    expect(statusLead("sent")).toBe("已发送给");
    expect(statusLead("pending_input")).toBe("待补充 ·");
    handoverStore.ingest({ specialist_id: "s2", specialist_name: "B", status: "sent" });
    handoverStore.ingest({ specialist_id: "s2", specialist_name: "B2", status: "applied" });
    const names = handoverStore.snapshot().filter((n) => n.specialistId === "s2");
    expect(names).toHaveLength(1);
    expect(names[0]?.specialistName).toBe("B2");
  });
});
