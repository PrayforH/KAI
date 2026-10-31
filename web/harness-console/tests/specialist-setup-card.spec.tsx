import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  join(process.cwd(), "src/components/specialist-setup-card.tsx"),
  "utf8",
);
const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

describe("pending_input setup card", () => {
  it("offers the three frequencies, a notify channel and the primary action", () => {
    expect(source).toContain("{ minutes: 60, label: \"每小时\" }");
    expect(source).toContain("{ minutes: 360, label: \"每 6 小时\" }");
    expect(source).toContain("{ minutes: 1440, label: \"每天\" }");
    expect(source).toContain("站内消息");
    expect(source).toContain("保存并启用");
  });

  it("saves config first, then activates with the returned revision", () => {
    const configIndex = source.indexOf("/config`");
    const statusIndex = source.indexOf("/status`");
    expect(configIndex).toBeGreaterThan(-1);
    expect(statusIndex).toBeGreaterThan(configIndex);
    // 第二步必须用第一步返回的 revision（CAS 才不会冲突）。
    expect(source).toContain("expectedRevision: configured.revision");
    expect(source).toContain("schedule_minutes: frequency");
    expect(source).toContain('notify_channel: "inbox"');
  });

  it("then reports the first execution as queued and refreshes the bot", () => {
    expect(source).toContain("已启用，首次执行排队中");
    expect(source).toContain("onReady?.()");
    expect(page).toContain("<SpecialistSetupCard");
    expect(page).toContain("onReady={() => setBotRevision((current) => current + 1)}");
  });

  it("stays out of the way for running bots and duty-less bots", () => {
    expect(source).toContain('if (!SETUP_STATUSES.has(status)) return null;');
    expect(source).toContain('data-state="no-duty"');
    expect(source).toContain("先让主助理给它派一件事");
  });
});
