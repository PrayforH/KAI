"use client";
import { ConfigurationIcon as Icon } from "./configuration-icon";
import Link from "next/link";
import type { StudioDraft, StudioSection } from "../../lib/agent-studio";
import { skillCreatorHref } from "../../lib/skill-creator-launch";
import styles from "./agenta-workspace.module.css";
export function AgentaConfiguration({
  draft,
  dirty,
  saving,
  writable,
  onEdit,
  onSave,
  onCode,
  onOpenMcp,
  onAddMcp,
  onCollapse,
}: {
  draft: StudioDraft;
  dirty: boolean;
  saving: boolean;
  writable: boolean;
  onEdit: (section: StudioSection) => void;
  onSave: () => void;
  onPublish: () => void;
  onCode: () => void;
  onBuildChat: () => void;
  onOpenMcp: () => void;
  onAddMcp: () => void;
  onCollapse?: () => void;
}) {
  const toolSources = [
    { id: "builtin", icon: "tools" as const, label: "内置工具", count: draft.builtinTools.length, names: draft.builtinTools.join("、") },
    { id: "mcp", icon: "mcp" as const, label: "MCP 服务", count: draft.mcpServers.length, names: draft.mcpServers.join("、") },
    { id: "python", icon: "code" as const, label: "Python 算子", count: draft.pythonTools.length, names: draft.pythonTools.map((tool) => tool.name).join("、") },
  ];
  return (
    <section className={styles.configuration} aria-label="智能体配置">
      <header className={styles.configHeader}>
        <strong>配置</strong>
        <div>
          <button disabled={!draft.id} onClick={onCode} title="查看这份配置的代码视图">
            代码
          </button>
          <button disabled={!dirty || saving || !writable} onClick={onSave}>
            {saving ? "保存中…" : "保存"}
          </button>
          {onCollapse && <button className={styles.configCollapse} aria-label="收起配置栏" title="收起配置栏" aria-expanded="true" onClick={onCollapse}><svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="2" /><path d="M8 4v12m5-9-3 3 3 3" /></svg></button>}
        </div>
      </header>
      <div className={styles.configScroll}>
        <button className={styles.configRow} onClick={() => onEdit("identity")}>
          <span className={styles.rowLabel}><Icon name="model" /><strong>模型</strong></span>
          <small>
            {draft.model || "选择模型"}
          </small>
          <Icon name="chevron" className={styles.chevron} />
        </button>
        <details className={styles.configGroup} open>
          <summary>
            <span className={styles.rowLabel}><Icon name="file" /><strong>指令</strong></span>
            <small>1 个文件</small>
            <Icon name="chevron" className={styles.chevron} />
          </summary>
          <button className={styles.fileRow} onClick={() => onEdit("prompt")}>
            <span className={styles.fileIcon}><Icon name="file" /></span>
            <span>
              <strong>
                AGENTS.md <small>指令</small>
              </strong>
              <p>
                {draft.systemPrompt?.slice(0, 160) ||
                  "定义智能体的职责、边界和输出要求"}
              </p>
            </span>
            <Icon name="chevron" className={styles.chevron} />
          </button>
        </details>
        <details className={styles.configGroup} open>
          <summary>
            <span className={styles.rowLabel}><Icon name="tools" /><strong>工具</strong></span>
            <small>
              {draft.builtinTools.length +
                draft.mcpServers.length +
                draft.pythonTools.length}{" "}
              个工具
            </small>
            <Icon name="chevron" className={styles.chevron} />
          </summary>
          <button
            className={styles.addRow}
            onClick={() => onEdit("capabilities")}
          >
            <Icon name="plus" /> 添加工具与集成
          </button>
          {/* One row per source. Every per-tool row opened the same editor, so
              enumerating them only lengthened the panel; the editor owns the
              detail and the names stay on the row's title. */}
          {toolSources.map((source) => (
            <button
              key={source.id}
              className={styles.toolRow}
              title={source.names || undefined}
              onClick={() => onEdit("capabilities")}
            >
              <span className={styles.toolIcon}><Icon name={source.icon} /></span>
              <strong>{source.label}</strong>
              <small>{source.count ? `${source.count} 项` : "未启用"}</small>
              <Icon name="chevron" className={styles.chevron} />
            </button>
          ))}
        </details>
        <div className={styles.configRowGroup}>
          <button className={styles.configRowMain} onClick={onOpenMcp}>
            <span className={styles.rowLabel}><Icon name="mcp" /><strong>MCP 服务器</strong></span>
            <small>{draft.mcpServers.length ? `${draft.mcpServers.length} 项已启用` : "无"}</small>
            <Icon name="chevron" className={styles.chevron} />
          </button>
          <button
            type="button"
            className={styles.rowAdd}
            aria-label="添加 MCP 服务器"
            title="添加 MCP 服务器"
            onClick={onAddMcp}
          >
            ＋
          </button>
        </div>
        <details className={styles.configGroup} open={draft.skills.length > 0}>
          <summary>
            <span className={styles.rowLabel}><Icon name="skill" /><strong>Skills</strong></span>
            <small>{draft.skills.length || "无"}</small>
            <Icon name="chevron" className={styles.chevron} />
          </summary>
          {draft.skills.map((skill) => (
            <button
              key={skill.name}
              className={styles.fileRow}
              onClick={() => onEdit("skills")}
            >
              <span className={styles.fileIcon}><Icon name="skill" /></span>
              <span>
                <strong>{skill.name}</strong>
                <p>{skill.description}</p>
              </span>
              <Icon name="chevron" className={styles.chevron} />
            </button>
          ))}
          <button className={styles.addRow} onClick={() => onEdit("skills")}>
            <Icon name="plus" /> 添加、导入或编辑 Skill
          </button>
          <Link
            className={styles.addRow}
            href={skillCreatorHref("agent", {
              agentDraftId: draft.id,
              agentLabel: draft.displayName,
            })}
          >
            使用 skill-creator 创建 ↗
          </Link>
        </details>
        <button
          className={styles.configRow}
          onClick={() => onEdit("capabilities")}
        >
          <span className={styles.rowLabel}><strong>文件与知识</strong></span>
          <small>{draft.knowledgeReferences.length} 项知识引用</small>
          <Icon name="knowledgeOpen" className={styles.configTrail} />
        </button>
        <button
          className={styles.configRow}
          onClick={() => onEdit("orchestration")}
        >
          <span className={styles.rowLabel}><Icon name="agent" /><strong>Subagents</strong></span>
          <small>{draft.subagents.length} 个协作角色</small>
          <Icon name="chevron" className={styles.chevron} />
        </button>
        <button className={styles.configRow} onClick={() => onEdit("runtime")}>
          <span className={styles.rowLabel}><Icon name="settings" /><strong>高级设置</strong></span>
          <small>{draft.runtime || "运行时、权限与沙箱"}</small>
          <Icon name="chevron" className={styles.chevron} />
        </button>

      </div>
    </section>
  );
}
