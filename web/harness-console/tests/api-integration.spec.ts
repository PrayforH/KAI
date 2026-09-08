import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  join(process.cwd(), "src/components/api-integration.tsx"),
  "utf8",
);
const settings = readFileSync(
  join(process.cwd(), "src/app/settings/page.tsx"),
  "utf8",
);
const clipboard = readFileSync(
  join(process.cwd(), "src/lib/clipboard.ts"),
  "utf8",
);

describe("API integration settings", () => {
  it("provides scoped key management and safe one-time secret display", () => {
    expect(settings).toContain('id: "api"');
    expect(settings).toContain("<ApiIntegration />");
    expect(source).toContain('"tasks:read"');
    expect(source).toContain('"tasks:write"');
    expect(source).toContain('"studio:read"');
    expect(source).toContain("完整密钥只在创建后显示一次");
    expect(source).toContain("<SecretInput value={created.secret}");
    expect(source).not.toContain("localStorage");
  });

  it("documents X-API-Key usage without embedding the generated secret", () => {
    expect(source).toContain("X-API-Key: $AXIS_API_KEY");
    expect(source).toContain("/v1/agents");
    expect(source).toContain('request<{ baseUrl: string }>("/api/auth/api-config")');
    expect(source).not.toContain("X-API-Key: ${created.secret}");
  });

  it("supports private-network HTTP copying with a visible failure state", () => {
    expect(source).toContain("writeTextToClipboard");
    expect(source).toContain("复制失败，请选中文本手动复制");
    expect(source).not.toContain("await navigator.clipboard.writeText(value)");
    expect(clipboard).toContain("navigator.clipboard?.writeText");
    expect(clipboard).toContain('document.execCommand("copy")');
  });

  it("explains the complete session and run question flow", () => {
    expect(source).toContain("问答请求预览");
    expect(source).toContain("Session 是一段连续对话");
    expect(source).toContain('/sessions/\\${SESSION_ID}/runs');
    expect(source).toContain('/runs/\\${RUN_ID}/events');
    expect(source).toContain("message.delta");
    expect(source).toContain("如何追问？");
  });
});
