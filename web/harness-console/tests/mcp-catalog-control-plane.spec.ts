import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const component = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/mcp-catalog-control-plane.tsx",
  ),
  "utf8",
);
const styles = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/mcp-catalog-control-plane.module.css",
  ),
  "utf8",
);
const navigation = readFileSync(
  join(process.cwd(), "src/components/workspace-navigation.tsx"),
  "utf8",
);
const page = readFileSync(
  join(process.cwd(), "src/app/studio/capabilities/page.tsx"),
  "utf8",
);

describe("MCP capability catalog", () => {
  it("has a discoverable Studio navigation entry and dedicated page", () => {
    expect(navigation).toContain('href: "/studio/capabilities"');
    expect(navigation).toContain('label: "MCP 能力"');
    expect(page).toContain('<StudioCapabilityManager defaultTab="mcp"');
  });

  it("supports governed registration, impact inspection, disable and deletion", () => {
    expect(component).toContain("studioClient.upsertMcp");
    expect(component).toContain('studioClient.catalogImpact("mcp", reference)');
    expect(component).toContain("studioClient.disableMcp");
    expect(component).toContain("studioClient.deleteMcp");
    expect(component).toContain("确认停用？");
    expect(component).toContain("永久删除");
    expect(component).toContain('EDITABLE_PLATFORM_MCP_REFERENCES = new Set(["tavily-readonly"])');
    expect(component).toContain("selectedDetail.ownerUserId");
    expect(component).toContain("EDITABLE_PLATFORM_MCP_REFERENCES.has(selectedDetail.reference)");
  });

  it("dismisses card action popovers when clicking outside", () => {
    expect(component).toContain("useDismissablePopovers()");
    expect(component).toContain("data-dismiss-on-outside");
  });

  it("uses one centered authoring page for MCP and knowledge connections", () => {
    expect(component).toContain("styles.editorBackdrop");
    expect(component).toContain('aria-labelledby="catalog-editor-title"');
    expect(component).toContain('role="dialog"');
    expect(component).toContain("useDialogFocus");
    expect(component).toContain("editorDialogRef");
    expect(component).toContain("syncDialogRef");
    expect(component).toContain("deleteDialogRef");
    expect(component).toContain("closeEditor");
    expect(styles).toMatch(/\.editorBackdrop\s*\{[^}]*inset:\s*44px 0 0 var\(--app-sidebar-expanded-width\);/s);
    expect(styles).toMatch(/\.editorBackdrop \.editor\s*\{[^}]*width:\s*min\(1160px, 100%\);/s);
  });

  it("uses the same centered catalog toolbar and single-column row surface as Skills", () => {
    expect(component).toContain("catalogToolbar");
    expect(component).toContain("刷新目录");
    expect(component).toContain("capabilityGlyph");
    expect(styles).toContain("width: min(1160px, 100%)");
    expect(styles).toMatch(/\.group\s*\{[^}]*border:\s*1px solid var\(--line\);/s);
  });

  it("opens MCP details in a centered dismissible modal", () => {
    expect(component).toContain("detailBackdrop");
    expect(component).toContain('aria-modal="true"');
    expect(component).toContain("event.target === event.currentTarget");
    expect(styles).toMatch(/\.detailBackdrop\s*\{[^}]*place-items:\s*center/s);
    expect(styles).toMatch(/\.detailDrawer\s*\{[^}]*width:\s*min\(720px, 100%\)/s);
  });

  it("authorizes MCP access to explicit network-compatible execution profiles", () => {
    expect(component).toContain("allowedProfileIds");
    expect(component).toContain("允许在哪些 Execution Profile 中使用");
    expect(component).toContain("与 MCP 定义原子保存");
    expect(component).toContain("profile.networkAccess.includes");
    expect(component).toContain("生产授权前，请确认该 Sandbox 能访问此内网地址");
    expect(component).toContain("尚未授权 Profile");
  });

  it("discovers an address and supports multi-tool selection", () => {
    expect(component).toContain("studioClient.discoverMcp");
    expect(component).toContain("MCP_IDENTIFIER_PATTERN");
    expect(component).toContain("支持连字符和单下划线");
    expect(component).toContain("服务名可保留单下划线");
    expect(component).toContain("initialize 和 tools/list");
    expect(component).toContain("检测地址");
    expect(component).toContain("TRANSPORT_LABELS");
    expect(component).toContain("已自动识别");
    expect(component).toContain("toggleTool");
    expect(component).toContain("全选");
    expect(component).toContain("清空");
    expect(component).toContain('type="search"');
  });

  it("groups real connection fields and keeps public headers separate from secrets", () => {
    for (const title of [
      "01",
      "基本信息",
      "02",
      "连接配置",
      "03",
      "鉴权",
      "04",
      "运行边界",
      "05",
      "连接测试与工具",
    ]) {
      expect(component).toContain(title);
    }
    expect(component).toContain("customHeaderRows");
    expect(component).toContain("customHeadersFromRows");
    expect(component).toContain("自定义请求头（可选）");
    expect(component).toContain("MANAGED_AUTH_HEADER_NAMES");
    expect(component).toContain("密钥、Token 和 Cookie 不能放入自定义请求头");
    expect(component).toContain("自动检测");
    expect(styles).toMatch(/\.formSectionTitle\s*\{[^}]*grid-template-columns:\s*20px minmax\(0, 1fr\);/s);
    expect(styles).toMatch(/\.formSectionTitle\s*>\s*span\s*\{[^}]*font:\s*700 9px\/1\.2/s);
    expect(styles).toContain("font-variant-numeric: tabular-nums");
    expect(component.match(/className=\{styles\.formSection\}/g)).toHaveLength(5);
    expect(styles).toMatch(/\.formSection\s*\{[^}]*border-radius:\s*12px;[^}]*background:\s*var\(--panel\);/s);
    expect(styles).toMatch(/@media \(max-width: 680px\)[\s\S]*?\.formSection\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\);/s);
  });

  it("explains which agents need resync after the reviewed tool list changes", () => {
    expect(component).toContain("result.impact.draftIds");
    expect(component).toContain("这些智能体需要同步");
    expect(component).toContain("不会自动获得新增工具");
    expect(component).toContain("/studio/agents?draft=");
    expect(component).toContain("section=capabilities");
    expect(component).toContain("去智能体更新");
  });

  it("configures required credentials without echoing stored secret values", () => {
    expect(component).toContain("studioClient.configureMcpCredential");
    expect(component).toContain("studioClient.listMcpCredentials");
    expect(component).toContain("<SecretInput");
    expect(component).toContain('revealLabel="认证凭据"');
    expect(component).toContain("凭据已加密保存；为安全起见不会回显原值");
    expect(component).not.toContain('placeholder="sk-');
  });
});
