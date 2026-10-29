"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { ActionMenuPopover } from "./action-menu-popover";
import {
  projectClient,
  studioClient,
  type StudioCapabilities,
} from "../lib/studio-client";
import type { ProjectSpaceState } from "./use-project-space";
import { ProjectModal } from "./project-resource-dialogs";
import { errorText } from "./project-space-shared";
import styles from "./project-home.module.css";

function ProjectControlIcon({ name }: { name: "plus" | "right" | "down" | "file" }) {
  const path = { plus: "M8 3v10M3 8h10", right: "m6 4 4 4-4 4", down: "m4 6 4 4 4-4", file: "M4 2.5h5l3 3v8H4zM9 2.5v3h3" }[name];
  return <svg className={styles.controlIcon} viewBox="0 0 16 16" aria-hidden="true"><path d={path} /></svg>;
}

function ProjectCardSection({ heading, children, actions, label }: {
  heading: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  label: string;
}) {
  return <div className={styles.cardSection}>
    <div className={styles.sectionHead}>{heading}</div>
    <div className={styles.cardContent} role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
    {actions && <div className={styles.cardActions}>{actions}</div>}
  </div>;
}

export function ProjectContextRail({ space }: { space: ProjectSpaceState }) {
  const {
    project, resources, setDialog, knowledgeNames, skillNames,
  } = space;
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const skillTrigger = useRef<HTMLButtonElement>(null);
  const [connectorNames, setConnectorNames] = useState<Record<string, string>>({});
  useEffect(() => {
    let active = true;
    studioClient.capabilities().then((catalog) => {
      if (active) setConnectorNames(Object.fromEntries(catalog.mcpServers.map((item) => [item.reference, item.label])));
    }).catch(() => { /* saved references remain visible if the catalog is unavailable */ });
    return () => { active = false; };
  }, []);
  if (!project || !resources) return null;
  const hasInstructions = Boolean(project.instructions?.trim());
  const hasFiles = Boolean(resources.documents.length + resources.links.length);
  return (
    <aside data-project-resources className={styles.contextRail} aria-label="项目配置与资源">
      <section className={styles.contextCard} data-card="instructions">
        <ProjectCardSection label="项目指令内容" heading={
          <button className={styles.cardHeading} onClick={() => setDialog("instructions")}>
            指令 <ProjectControlIcon name="right" />
          </button>
        } actions={
          <button className={styles.button} onClick={() => setDialog("instructions")}>
            {!hasInstructions && <ProjectControlIcon name="plus" />}{hasInstructions ? "编辑指令" : "添加指令"}
          </button>
        }>
          {hasInstructions && <p className={styles.instructionPreview}>{project.instructions}</p>}
        </ProjectCardSection>
        <div className={styles.cardDivider} />
        <ProjectCardSection label="项目连接器列表" heading={<>
          <h2>连接器 <small className={styles.count} aria-label={project.mcpReferences?.length ? `已关联 ${project.mcpReferences.length} 个连接器` : undefined}>
            MCP{project.mcpReferences?.length ? ` · ${project.mcpReferences.length}` : ""}
          </small></h2>
          <button className={styles.textLink} aria-label="添加项目连接器" onClick={() => setDialog("connectors")}>
            <ProjectControlIcon name="plus" />添加
          </button>
        </>} actions={!!project.mcpReferences?.length &&
          <button className={styles.textLink} onClick={() => setDialog("connectors")}>
            查看与管理 <ProjectControlIcon name="right" />
          </button>
        }>
          {!!project.mcpReferences?.length && <ul className={styles.compactList}>
            {project.mcpReferences.map((reference) => <li key={reference}>{connectorNames[reference] ?? reference}</li>)}
          </ul>}
        </ProjectCardSection>
      </section>
      <section className={styles.contextCard} data-card="resources">
        <ProjectCardSection label="项目文件和链接列表" heading={
          <button className={styles.cardHeading} onClick={() => setDialog("files")}>
            文件和资源 <ProjectControlIcon name="right" />
          </button>
        } actions={
          <button className={styles.button} onClick={() => setDialog("files")}>
            {!hasFiles && <ProjectControlIcon name="plus" />}{hasFiles ? "管理文件与链接" : "上传文件或添加链接"}
          </button>
        }>
          {hasFiles && <ul className={styles.compactList}>
            {resources.documents.map((item) => <li key={item.inputArtifactId}>
              <ProjectControlIcon name="file" />
              <a href={`/api/input-artifacts/${encodeURIComponent(item.inputArtifactId)}/content?inline=1`} target="_blank" rel="noreferrer">
                {item.name}
              </a>
            </li>)}
            {resources.links.map((item) => <li key={item.linkId}>
              <ProjectControlIcon name="right" />
              <a href={item.url} target="_blank" rel="noreferrer">{item.title || item.url}</a>
            </li>)}
          </ul>}
        </ProjectCardSection>
        <div className={styles.cardDivider} />
        <ProjectCardSection label="项目技能列表" heading={
          <button className={styles.cardHeading} onClick={() => setDialog("skills")}>
            技能 <ProjectControlIcon name="right" />
          </button>
        } actions={<>
          <button ref={skillTrigger} className={styles.button} aria-label="添加项目技能" aria-haspopup="menu" aria-expanded={skillMenuOpen}
            onClick={() => setSkillMenuOpen(value => !value)}
            onKeyDown={event => { if (event.key === "ArrowDown") { event.preventDefault(); setSkillMenuOpen(true); } }}>
            <ProjectControlIcon name="plus" />添加 <span className={skillMenuOpen ? styles.expandedChevron : undefined}><ProjectControlIcon name="down" /></span>
          </button>
          {skillMenuOpen && <ActionMenuPopover anchor={skillTrigger} align="start" label="添加项目技能" onClose={() => setSkillMenuOpen(false)}>
            <button type="button" role="menuitem" onClick={() => { setSkillMenuOpen(false); setDialog("skills"); }}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6Z"/><path d="m8 12 3 3 5-6"/></svg>
              <span><strong>从平台技能添加</strong><small>选择平台维护的技能，供项目任务使用</small></span>
            </button>
          </ActionMenuPopover>}
        </>}>
          {!!project.skillReferences?.length && <ul className={styles.compactList}>
            {project.skillReferences.map((ref) => <li key={ref}>{skillNames.get(ref) ?? ref}</li>)}
          </ul>}
        </ProjectCardSection>
      </section>
      <section className={styles.contextCard} data-card="knowledge">
        <ProjectCardSection label="项目知识库列表" heading={<>
          <h2>知识库</h2>
          <button className={styles.textLink} onClick={() => setDialog("knowledge")}>
            <ProjectControlIcon name="plus" />关联
          </button>
        </>}>
          {project.knowledgeReferences?.length ? <ul className={styles.compactList}>
            {project.knowledgeReferences.map((ref) => <li key={ref}>
              <Link href={`/studio/knowledge/${encodeURIComponent(ref)}`}>{knowledgeNames.get(ref) ?? ref}</Link>
            </li>)}
          </ul> : <p className={styles.muted}>暂无知识库</p>}
        </ProjectCardSection>
      </section>
    </aside>
  );
}

export function ProjectConnectorsDialog({
  space,
}: {
  space: ProjectSpaceState;
}) {
  const { project, setDialog, refreshResources, setNotice } = space;
  const [items, setItems] = useState<StudioCapabilities["mcpServers"]>([]);
  const [selected, setSelected] = useState(project?.mcpReferences ?? []);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    studioClient
      .capabilities()
      .then((catalog) => {
        if (active)
          setItems(
            catalog.mcpServers.filter(
              (item) =>
                item.enabled &&
                item.endpointUrl &&
                item.visibility !== "selected",
            ),
          );
      })
      .catch((cause) => {
        if (active) setError(errorText(cause));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);
  if (!project) return null;
  const unavailable = selected.filter(
    (ref) => !items.some((item) => item.reference === ref),
  );
  return (
    <ProjectModal
      title="项目连接器"
      subtitle="关联已有 MCP，使用当前执行用户的连接凭据。"
      onClose={() => setDialog(null)}
    >
      <form
        className={styles.dialogContent}
        onSubmit={async (event) => {
          event.preventDefault();
          if (saving || loading) return;
          setSaving(true);
          setError("");
          try {
            await projectClient.update(project.projectId, {
              mcpReferences: selected,
            });
            const refreshed = await refreshResources();
            if (!refreshed) throw new Error("关联已提交，但未能确认保存结果，请重试。");
            const saved = refreshed.project.mcpReferences ?? [];
            if (saved.length !== selected.length || selected.some((ref) => !saved.includes(ref)))
              throw new Error("连接器关联未完整保存，请重试。");
            setNotice(saved.length ? `已保存 ${saved.length} 个项目连接器。` : "已移除项目连接器关联。");
            setDialog(null);
          } catch (cause) {
            setError(errorText(cause));
          } finally {
            setSaving(false);
          }
        }}
      >
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
        {loading ? (
          <p role="status">正在读取连接器…</p>
        ) : (
          <>
            {!items.length && (
              <p className={styles.muted}>暂无可关联的连接器。</p>
            )}
            {[
              ...items.map((item) => ({
                reference: item.reference,
                label: item.label,
              })),
              ...unavailable.map((ref) => ({
                reference: ref,
                label: `${ref}（已不可用，请移除）`,
              })),
            ].map((item) => (
              <label className={styles.connectorOption} key={item.reference}>
                <input
                  type="checkbox"
                  checked={selected.includes(item.reference)}
                  disabled={saving}
                  onChange={(event) => {
                    const checked = event.currentTarget.checked;
                    setSelected((current) => checked
                      ? [...new Set([...current, item.reference])]
                      : current.filter((ref) => ref !== item.reference));
                  }}
                />
                {item.label}
              </label>
            ))}
          </>
        )}
        <p className={styles.muted}>
          连接器的创建、凭据和授权在{" "}
          <Link href="/studio/agents">智能体的工具配置</Link>
          中维护。此处只关联，智能体自带工具继续保留。
        </p>
        <div className={styles.actions}>
          <button
            className={styles.button}
            type="button"
            onClick={() => setDialog(null)}
          >
            取消
          </button>
          <button className={styles.primary} disabled={saving || loading}>
            {saving ? "保存中…" : "保存关联"}
          </button>
        </div>
      </form>
    </ProjectModal>
  );
}
