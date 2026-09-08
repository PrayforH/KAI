import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const settings = readFileSync(
  join(process.cwd(), "src/app/settings/page.tsx"),
  "utf8",
);
const management = readFileSync(
  join(process.cwd(), "src/components/model-management.tsx"),
  "utf8",
);
const secretInput = readFileSync(
  join(process.cwd(), "src/components/secret-input.tsx"),
  "utf8",
);
const modelStyles = readFileSync(
  join(process.cwd(), "src/components/model-management.module.css"),
  "utf8",
);
const themeStyles = readFileSync(
  join(process.cwd(), "src/app/codex-theme.css"),
  "utf8",
);

describe("model management settings", () => {
  it("shows the control plane only to workspace administrators", () => {
    expect(settings).toContain(
      'membership.role === "owner" || membership.role === "admin"',
    );
    expect(settings).toContain('["models", "api"]');
    expect(settings).toContain("{canManageModels && (");
    expect(settings).toContain("<ModelManagement />");
  });

  it("supports conversation, vision, image generation and isolated video generation", () => {
    expect(management).toContain(
      'type ModelType = "chat" | "vision" | "image_generation" | "video_generation"',
    );
    expect(management).toContain("图像生成");
    expect(management).toContain("视频生成");
    expect(management).toContain("MiniMax H3（229 内网）");
    expect(management).toContain('authScheme: "bearer" | "x-api-key" | "none"');
    expect(management).toContain("不参与对话路由");
  });

  it("never loads or displays a stored API key", () => {
    expect(management).toContain("API Key 加密保存且不回传浏览器");
    expect(management).toContain("已安全保存；留空则保持不变");
    expect(management).not.toContain("model.apiKey");
  });

  it("uses an accessible reveal control for keys and passwords", () => {
    expect(management).toContain('<SecretInput name="apiKey"');
    expect(settings.match(/<SecretInput/g)).toHaveLength(3);
    expect(secretInput).toContain('type={visible ? "text" : "password"}');
    expect(secretInput).toContain("aria-pressed={visible}");
    expect(secretInput).toContain('type="button"');
  });

  it("permanently deletes frontend-managed models", () => {
    expect(management).toContain("model.deletable &&");
    expect(management).toContain("/permanent?expectedRevision=");
    expect(management).toContain("永久删除模型");
    expect(management).toContain("控制面配置");
    expect(management).not.toContain("恢复服务器配置");
  });

  it("keeps dropdown arrows away from the right border", () => {
    expect(themeStyles).toContain("background-position: right 18px center !important");
    expect(themeStyles).toContain("padding-inline-end: 44px !important");
    expect(modelStyles).toContain("padding: 0 44px 0 9px");
  });
});
