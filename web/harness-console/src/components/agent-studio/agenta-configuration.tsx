"use client";
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
  onPublish,
  onCode,
  onBuildChat,
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
}) {
  const operations = `/studio/agents/${encodeURIComponent(draft.name)}?${new URLSearchParams({ draft: draft.id, section: "automation" })}`;
  return (
    <section className={styles.configuration} aria-label="智能体配置">
      <header className={styles.configHeader}>
        <strong>Configuration</strong>
        <div>
          <button
            disabled={!draft.id || saving || !writable}
            onClick={onPublish}
          >
            发布
          </button>
          <button disabled={!dirty || saving || !writable} onClick={onSave}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </header>
      <div className={styles.configScroll}>
        <button className={styles.configRow} onClick={() => onEdit("identity")}>
          <span>
            ◇ <strong>Model & harness</strong>
          </span>
          <small>
            {draft.runtime} · {draft.model || "选择模型"} ›
          </small>
        </button>
        <details className={styles.configGroup} open>
          <summary>
            <strong>Instructions</strong>
            <small>1 file</small>
          </summary>
          <button className={styles.fileRow} onClick={() => onEdit("prompt")}>
            <span className={styles.fileIcon}>≡</span>
            <span>
              <strong>
                AGENTS.md <small>指令</small>
              </strong>
              <p>
                {draft.systemPrompt?.slice(0, 160) ||
                  "定义智能体的职责、边界和输出要求"}
              </p>
            </span>
            <span>›</span>
          </button>
        </details>
        <details className={styles.configGroup} open>
          <summary>
            <strong>Tools</strong>
            <small>
              {draft.builtinTools.length +
                draft.mcpServers.length +
                draft.pythonTools.length}{" "}
              tools
            </small>
          </summary>
          <button
            className={styles.addRow}
            onClick={() => onEdit("capabilities")}
          >
            ＋ 添加工具与集成
          </button>
          {draft.builtinTools.map((tool) => (
            <button
              key={tool}
              className={styles.toolRow}
              onClick={() => onEdit("capabilities")}
            >
              <span className={styles.toolIcon}>io</span>
              <strong>{tool}</strong>
              <small>built-in</small>
              <span>›</span>
            </button>
          ))}
          {draft.mcpServers.map((tool) => (
            <button
              key={tool}
              className={styles.toolRow}
              onClick={() => onEdit("capabilities")}
            >
              <span className={styles.toolIcon}>↗</span>
              <strong>{tool}</strong>
              <small>MCP</small>
              <span>›</span>
            </button>
          ))}
          {draft.pythonTools.map((tool) => (
            <button
              key={tool.name}
              className={styles.toolRow}
              onClick={() => onEdit("capabilities")}
            >
              <span className={styles.toolIcon}>py</span>
              <strong>{tool.name}</strong>
              <small>Python</small>
            </button>
          ))}
        </details>
        <details className={styles.configGroup} open={draft.skills.length > 0}>
          <summary>
            <strong>Skills</strong>
            <small>{draft.skills.length || "None"}</small>
          </summary>
          {draft.skills.map((skill) => (
            <button
              key={skill.name}
              className={styles.fileRow}
              onClick={() => onEdit("skills")}
            >
              <span className={styles.fileIcon}>S</span>
              <span>
                <strong>{skill.name}</strong>
                <p>{skill.description}</p>
              </span>
              <span>›</span>
            </button>
          ))}
          <button className={styles.addRow} onClick={() => onEdit("skills")}>
            ＋ 添加、导入或编辑 Skill
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
          <strong>Files & knowledge</strong>
          <small>{draft.knowledgeReferences.length} 项知识引用 ›</small>
        </button>
        <button
          className={styles.configRow}
          onClick={() => onEdit("orchestration")}
        >
          <strong>Subagents</strong>
          <small>{draft.subagents.length} 个协作角色 ›</small>
        </button>
        <button className={styles.configRow} onClick={() => onEdit("runtime")}>
          <strong>Advanced</strong>
          <small>运行时、权限与沙箱 ›</small>
        </button>
        <div className={styles.groupLabel}>Triggers</div>
        {draft.id ? (
          <>
            <Link className={styles.configRow} href={operations}>
              <strong>Subscriptions</strong>
              <small>应用事件与 Webhook ›</small>
            </Link>
            <Link className={styles.configRow} href={operations}>
              <strong>Schedules</strong>
              <small>Cron 与时区 ›</small>
            </Link>
          </>
        ) : (
          <p className={styles.hint}>创建草稿后配置自动运行。</p>
        )}
        <div className={styles.groupLabel}>Developer</div>
        <button
          className={styles.configRow}
          disabled={!draft.id}
          onClick={onCode}
        >
          <strong>DeepAgents 代码</strong>
          <small>文件树与代码差异 ›</small>
        </button>
        <button className={styles.configRow} onClick={onBuildChat}>
          <strong>对话构建</strong>
          <small>描述需求，审阅修改建议 ›</small>
        </button>
      </div>
    </section>
  );
}
