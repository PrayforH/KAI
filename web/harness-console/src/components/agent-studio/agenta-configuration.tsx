"use client";

import { ConfigurationIcon as Icon } from "./configuration-icon";
import type { McpOption, StudioDraft, StudioSection } from "../../lib/agent-studio";
import styles from "./agenta-workspace.module.css";

export type CapabilityFocus = "builtin" | "python" | "mcp";

export function AgentaConfiguration({ draft, dirty, saving, writable, onEdit, onSave, onCode, mcpOptions, onCollapse }: {
  draft: StudioDraft;
  dirty: boolean;
  saving: boolean;
  writable: boolean;
  onEdit: (section: StudioSection, target?: string) => void;
  onSave: () => void;
  onPublish: () => void;
  onCode: () => void;
  onBuildChat: () => void;
  onAddMcp: () => void;
  mcpOptions: McpOption[];
  onToggleMcp: (id: string) => void;
  onCollapse?: () => void;
}) {
  const rows = [
    { section: "identity", icon: "model", label: "模型与基本信息", count: "", description: draft.model || "选择模型" },
    { section: "prompt", icon: "file", label: "指令", count: "AGENTS.md", description: draft.systemPrompt || "定义智能体的职责、边界和输出要求" },
    { section: "capabilities", target: "builtin", icon: "tools", label: "内置工具", count: `${draft.builtinTools.length} 项`, description: draft.builtinTools.join("、") || "选择文件、终端与联网工具" },
    { section: "capabilities", target: "mcp", icon: "mcp", label: "MCP 服务器", count: `${draft.mcpServers.length} 个`, description: draft.mcpServers.map(id => mcpOptions.find(option => option.id === id)?.label || id).join("、") || "连接外部服务与工具" },
    { section: "knowledge", icon: "knowledge", label: "文件与知识", count: `${draft.knowledgeReferences.length} 个`, description: "选择智能体可检索的知识库" },
    { section: "skills", icon: "skill", label: "技能", count: `${draft.skills.length} 项`, description: draft.skills.map(skill => skill.name).join("、") || "添加可复用的工作流与领域技能" },
    { section: "capabilities", target: "python", icon: "code", label: "Python 算子", count: `${draft.pythonTools.length} 项`, description: draft.pythonTools.map(tool => tool.name).join("、") || "管理自定义 Python 工具" },
    { section: "orchestration", icon: "agent", label: "Subagents", count: `${draft.subagents.length} 个`, description: "设置协作角色与分工" },
    { section: "runtime", icon: "settings", label: "高级设置", count: "", description: draft.runtime || "运行时、权限与沙箱" },
  ] as const;
  return <section className={styles.configuration} aria-label="智能体配置">
    <header className={styles.configHeader}>
      <div className={styles.configViewSwitch} role="group" aria-label="配置与代码视图">
        <button type="button" aria-pressed="true">配置</button>
        <button type="button" aria-pressed="false" disabled={!draft.id} onClick={onCode} title="查看这份配置的代码视图">代码</button>
      </div>
      <div>
        <button type="button" disabled={!dirty || saving || !writable} onClick={onSave}>{saving ? "保存中…" : "保存"}</button>
        {onCollapse && <button type="button" className={styles.configCollapse} aria-label="收起配置栏" title="收起配置栏" aria-expanded="true" onClick={onCollapse}><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="2" /><path d="M8 4v12m5-9-3 3 3 3" /></svg></button>}
      </div>
    </header>
    <div className={`${styles.configScroll} ${styles.configCards}`}>
      {rows.map(row => <button type="button" key={`${row.section}-${"target" in row ? row.target : ""}`} className={styles.configCard} title={row.description} onClick={() => onEdit(row.section, "target" in row ? row.target : undefined)}>
        <span className={styles.configCardIcon}><Icon name={row.icon} /></span>
        <span className={styles.configCardCopy}><span><strong>{row.label}</strong><small>{row.count}</small></span><p>{row.description}</p></span>
        <Icon name="chevron" className={styles.chevron} />
      </button>)}
    </div>
  </section>;
}
