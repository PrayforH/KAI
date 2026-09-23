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
const workbench = readFileSync(
  join(process.cwd(), "src/components/agent-studio/agent-studio-workbench.tsx"),
  "utf8",
);

describe("MCP capability catalog", () => {
  it("is reached from the agent that owns it, not from the workspace navigation", () => {
    // MCP is an agent asset: the workspace nav no longer carries it, the old
    // route redirects, and the agent's Tools group owns the entry.
    expect(navigation).not.toContain('href: "/studio/capabilities"');
    // Registration is a drawer opened from the agent; there is no management
    // page and no nav entry for one.
    expect(workbench).toContain("setMcpStartInForm(true); setMcpManagerOpen(true);");
    // The panel row opens the catalog in a drawer and the + opens the form,
    // which is itself a drawer. The catalog view is a page layout, so mounting
    // it bare painted into the workbench.
    expect(workbench).toContain("setMcpStartInForm(true); setMcpManagerOpen(true);");
    expect(workbench).toContain("<McpCatalogControlPlane startInForm onClose={() => setMcpManagerOpen(false)}");
    // Dismissing the form closes the surface: falling back to the catalog page
    // would paint a full page where the drawer was.
    expect(component).toContain("const closeForm = () => {");
    expect(component).toContain("onClose?.();");
    expect(component).toContain("startInForm = false");
    expect(component).toContain("useState(startInForm)");
  });

  it("asks only for what a registration needs, and folds the rest away", () => {
    expect(component).toContain("<summary>高级设置（可选）</summary>");
    expect(component).toContain("styles.formAdvanced");
    // The governance knobs keep working, they are just out of the way by default.
    const start = component.indexOf("styles.formAdvanced");
    const advanced = component.slice(start, component.indexOf("</details>", start));
    for (const field of ["传输类型", "风险级别", "网络范围", "执行位置"]) {
      expect(advanced).toContain(field);
    }
  });

  it("supports governed registration, impact inspection, disable and deletion", () => {
    expect(component).toContain("studioClient.upsertMcp");
    expect(component).toContain('studioClient.catalogImpact("mcp", reference)');
    expect(component).toContain("studioClient.disableMcp");
    expect(component).toContain("studioClient.deleteMcp");
    expect(component).toContain("确认停用？");
    expect(component).toContain("永久删除");
    expect(component).toContain("const EDITABLE_PLATFORM_MCP_REFERENCES = new Set<string>();");
    expect(component).toContain("selectedDetail.ownerUserId");
    expect(component).toContain("EDITABLE_PLATFORM_MCP_REFERENCES.has(selectedDetail.reference)");
  });

  it("dismisses card action popovers when clicking outside", () => {
    expect(component).toContain("useDismissablePopovers()");
    expect(component).toContain("data-dismiss-on-outside");
  });

  it("authors a connection in the one right-hand drawer", () => {
    expect(component).toContain("styles.editorBackdrop");
    expect(component).toContain('aria-labelledby="catalog-editor-title"');
    expect(component).toContain('role="dialog"');
    expect(component).toContain("useDialogFocus");
    expect(component).toContain("editorDialogRef");
    expect(component).toContain("syncDialogRef");
    expect(component).toContain("deleteDialogRef");
    expect(component).toContain("closeEditor");
    expect(styles).toMatch(/\.editorBackdrop\s*\{[^}]*inset:\s*44px 0 0 var\(--app-sidebar-expanded-width\);/s);
    // The authoring page keeps the same measure as the catalog list (1160px)
    // rather than stretching across the window.
    // One drawer, and no leftover page-context rule fighting its width: the
    // block that kept the page's 1160px measure used to win by source order.
    expect(styles).toMatch(/\.editorBackdrop\s*\{[^}]*justify-items:\s*end;/s);
    expect(styles).toMatch(/\.editorBackdrop \.editor\s*\{[^}]*width:\s*clamp\(320px, 33vw, 560px\);/s);
    expect(styles).not.toMatch(/\.editorBackdrop \.editor\s*\{[^}]*width:\s*min\(1160px/s);
  });

  it("fills each form row instead of leaving an empty column beside a field", () => {
    // Short fields form a three-column row, so a row of three fills its card
    // instead of rendering with an empty column beside it.
    expect(styles).toMatch(
      /\.formSection\s*\{\s*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\);/s,
    );
    expect(styles).toMatch(
      /@media \(max-width: 1320px\)\s*\{\s*\.formSection\s*\{\s*grid-template-columns:\s*repeat\(2, minmax\(0, 1fr\)\);/s,
    );
  });

  it("keeps the light authoring surface distinguishable from its section cards", () => {
    expect(styles).toMatch(
      /data-color-mode="light"\]\) \.editorBackdrop,\s*:global\(html\[data-color-mode="light"\]\) \.editorBackdrop \.editor\s*\{\s*background:\s*#f7f7f8;/s,
    );
    expect(styles).toMatch(
      /data-color-mode="light"\]\) \.editorBackdrop \.formSection\s*\{\s*border-color:\s*#e4e4e7;/s,
    );
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

  it("keeps a new registration usable by defaulting its profile authorization", () => {
    // The form no longer hosts the profile card: the default is what keeps a
    // fresh registration usable (an unauthorized MCP is refused by every
    // profile), and profile governance has its own control plane.
    expect(component).toContain("allowedProfileIds");
    expect(component).not.toContain("允许在哪些 Execution Profile 中使用");
    expect(component).toContain('profile.sandboxProvider === "local"');
    expect(component).toContain("profile.networkAccess.includes");
    expect(component).toContain("allowedProfileIds,");
  });

  it("discovers an address and supports multi-tool selection", () => {
    expect(component).toContain("studioClient.discoverMcp");
    expect(component).toContain("MCP_IDENTIFIER_PATTERN");
    expect(component).toContain("支持连字符和单下划线");
    expect(component).toContain("const serverName = draft.serverName?.trim() || reference;");
    expect(component).not.toContain("<span>MCP 服务名</span>");
    expect(component).toContain("连接成功后选择需要开放的工具");
    expect(component).toContain("检测地址");
    expect(component).toContain("TRANSPORT_LABELS");
    expect(component).toContain("已自动识别");
    expect(component).toContain("toggleTool");
    expect(component).toContain("全选");
    expect(component).toContain("清空");
    expect(component).toContain('type="search"');
  });

  it("keeps the connection fields on one surface and secrets out of the headers", () => {
    // The numbered step cards are gone: the fields carry the form, and the
    // governance knobs sit inside one 高级设置 disclosure.
    expect(component).not.toContain("formSectionTitle");
    for (const field of ["引用标识", "显示名称", "能力说明", "鉴权方式"]) {
      expect(component).toContain(`<span>${field}</span>`);
    }
    expect(component).toContain("检测连接并识别工具");
    expect(component).toContain("自定义请求头（可选）");
    expect(component).toContain("MANAGED_AUTH_HEADER_NAMES");
    expect(component).toContain("密钥、Token 和 Cookie 不能放入自定义请求头");
    expect(component).toContain("自动检测");
    // One surface, and a narrower drawer for a shorter form.
    expect(styles).toMatch(/\.formSection\s*\{[^}]*border:\s*0;/s);
    // A third of the screen is enough for the fields that are left.
    expect(styles).toMatch(/\.editorBackdrop \.editor\s*\{[^}]*width:\s*clamp\(320px, 33vw, 560px\)/s);
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
