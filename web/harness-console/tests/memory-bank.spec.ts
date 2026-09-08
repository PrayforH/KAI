import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { type MemoryEntry, memoryClient } from "../src/lib/memory-client";

const component = readFileSync(
  join(process.cwd(), "src/components/memory-bank/memory-bank.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/memory-bank/memory-bank.module.css"),
  "utf8",
);
const settings = readFileSync(
  join(process.cwd(), "src/app/settings/page.tsx"),
  "utf8",
);

const entry = {
  entryId: "memory-1", version: 4, content: "偏好中文",
} as MemoryEntry;

describe("managed memory user surface", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("makes consent, provenance, correction and deletion explicit", () => {
    expect(settings).toContain("<MemoryBank embedded />");
    expect(component).toContain("<select");
    expect(component).toContain("loadSequence");
    expect(component).toContain("你决定智能体记住什么");
    expect(component).toContain("来源 ·");
    expect(component).toContain("置信度");
    expect(component).toContain("确认保存");
    expect(component).toContain("编辑");
    expect(component).toContain("删除后内容会立即清除");
    expect(component).toContain("导出 JSON");
    expect(component).toContain("敏感信息仍需逐条确认");
    expect(styles).toContain("@media(max-width:580px)");
  });

  it("sends the current version for confirmation, edits and deletion", async () => {
    const calls: Array<{ url: string; method: string; body?: unknown }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: String(init?.method),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      return init?.method === "DELETE" ? new Response(null, { status: 204 }) : Response.json(entry);
    });
    await memoryClient.confirm(entry);
    await memoryClient.update(entry, "偏好中文简报");
    await memoryClient.remove(entry);
    expect(calls).toEqual([
      { url: "/api/memory-bank/entries/memory-1/confirm", method: "POST", body: { expectedVersion: 4 } },
      { url: "/api/memory-bank/entries/memory-1", method: "PUT", body: { expectedVersion: 4, content: "偏好中文简报" } },
      { url: "/api/memory-bank/entries/memory-1?expectedVersion=4", method: "DELETE" },
    ]);
  });
});
