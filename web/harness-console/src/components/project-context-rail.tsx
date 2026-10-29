"use client";

import { useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import Link from "next/link";
import { ActionMenuPopover } from "./action-menu-popover";
import { projectClient, studioClient, type StudioCapabilities } from "../lib/studio-client";
import type { ProjectSpaceState } from "./use-project-space";
import { ProjectFilesDialog, ProjectModal, uploadProjectDocument } from "./project-resource-dialogs";
import { ProjectResourceIcon } from "./project-resource-icon";
import { errorText } from "./project-space-shared";
import styles from "./project-home.module.css";

function ProjectControlIcon({ name }: { name: "plus" | "right" | "down" | "edit" | "upload" | "link" }) {
  const path = {
    plus: "M8 3v10M3 8h10", right: "m6 4 4 4-4 4", down: "m4 6 4 4 4-4",
    edit: "m10 2 4 4M2 14l3-1L14 4l-3-3-9 10Z",
    upload: "M8 11V2m-4 4 4-4 4 4M2 10v4h12v-4",
    link: "m6 10 4-4M5 6 3 8a3 3 0 0 0 5 5l2-2M6 5l2-2a3 3 0 0 1 5 5l-2 2",
  }[name];
  return <svg className={styles.controlIcon} viewBox="0 0 16 16" aria-hidden="true"><path d={path} /></svg>;
}

function ProjectCardSection({ kind, heading, children, actions, label }: {
  kind: "instructions" | "connectors" | "files" | "skills" | "knowledge";
  heading: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  label: string;
}) {
  return <div className={styles.cardSection} data-resource-section={kind}>
    <div className={styles.sectionHead}>{heading}</div>
    {children && <div className={styles.cardContent} role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>}
    {actions && <div className={styles.cardActions}>{actions}</div>}
  </div>;
}

export function ProjectContextRail({ space }: { space: ProjectSpaceState }) {
  const { project, resources, setDialog, knowledgeNames, skillNames, refreshResources, setNotice } = space;
  const [skillMenuOpen, setSkillMenuOpen] = useState(false);
  const [uploadMenuOpen, setUploadMenuOpen] = useState(false);
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const skillTrigger = useRef<HTMLButtonElement>(null);
  const uploadTrigger = useRef<HTMLButtonElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [connectors, setConnectors] = useState<StudioCapabilities["mcpServers"]>([]);
  useEffect(() => {
    let active = true;
    studioClient.capabilities().then((catalog) => {
      if (active) setConnectors(catalog.mcpServers);
    }).catch(() => { /* Saved references retain a generic MCP icon and their name. */ });
    return () => { active = false; };
  }, []);
  if (!project || !resources) return null;
  const hasInstructions = Boolean(project.instructions?.trim());

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !project || uploading) return;
    setUploading(true);
    setUploadError("");
    try {
      const inputArtifactId = await uploadProjectDocument(file);
      await projectClient.attachDocument(project.projectId, inputArtifactId);
      if (!await refreshResources()) throw new Error("文件已上传，刷新项目资料失败，请重新打开项目。");
      setNotice(`已添加 ${file.name}`);
    } catch (cause) {
      setUploadError(errorText(cause));
    } finally {
      setUploading(false);
    }
  }

  return <>
    <aside data-project-resources className={styles.contextRail} aria-label="项目配置与资源">
      <section className={styles.contextCard} data-card="instructions">
        <ProjectCardSection kind="instructions" label="项目指令内容" heading={<>
          <button type="button" className={styles.cardHeading} onClick={() => setDialog("instructions")}>
            指令 <ProjectControlIcon name="right" />
          </button>
          {hasInstructions && <button type="button" className={styles.cardIconButton} aria-label="编辑指令" title="编辑指令" onClick={() => setDialog("instructions")}>
            <ProjectControlIcon name="edit" />
          </button>}
        </>} actions={!hasInstructions &&
          <button type="button" className={styles.button} onClick={() => setDialog("instructions")}>
            <ProjectControlIcon name="plus" />添加
          </button>
        }>
          {hasInstructions ? <p className={styles.instructionPreview}>{project.instructions}</p>
            : <p className={styles.muted}>指导助手如何在此项目的所有任务中响应。</p>}
        </ProjectCardSection>
        <div className={styles.cardDivider} />
        <ProjectCardSection kind="connectors" label="项目连接器列表" heading={<>
          <button type="button" className={styles.cardHeading} onClick={() => setDialog("connectors")}>
            连接器 <ProjectControlIcon name="right" />
          </button>
          <button type="button" className={styles.connectAction} aria-label="添加项目连接器" onClick={() => setDialog("connectors")}>连接</button>
        </>}>
          {!!project.mcpReferences?.length && <ul className={styles.connectorIcons} aria-label={`已关联 ${project.mcpReferences.length} 个连接器`}>
            {project.mcpReferences.map((reference) => {
              const connector = connectors.find(item => item.reference === reference);
              const name = connector?.label ?? reference;
              return <li key={reference}>
                <button type="button" className={styles.connectorIcon} aria-label={`管理连接器：${name}`} title={name} onClick={() => setDialog("connectors")}>
                  <ProjectResourceIcon kind="connector" name={`${reference} ${connector?.serverName ?? ""} ${name}`} />
                </button>
              </li>;
            })}
            <li><button type="button" className={styles.cardIconButton} aria-label="管理项目连接器" title="管理连接器" onClick={() => setDialog("connectors")}><ProjectControlIcon name="edit" /></button></li>
          </ul>}
        </ProjectCardSection>
      </section>
      <section className={styles.contextCard} data-card="resources">
        <ProjectCardSection kind="files" label="项目文件和链接列表" heading={
          <button type="button" className={styles.cardHeading} onClick={() => setDialog("files")}>
            文件和资源 <ProjectControlIcon name="right" />
          </button>
        } actions={<>
          <input ref={fileInput} type="file" className={styles.hiddenFile} aria-label="上传项目文件" disabled={uploading} onChange={event => void upload(event)} />
          <button type="button" ref={uploadTrigger} className={styles.button} aria-label="上传项目资源" aria-haspopup="menu" aria-expanded={uploadMenuOpen} disabled={uploading}
            onClick={() => setUploadMenuOpen(value => !value)} onKeyDown={event => { if (event.key === "ArrowDown") { event.preventDefault(); setUploadMenuOpen(true); } }}>
            <ProjectControlIcon name="upload" />{uploading ? "上传中…" : "上传"}<ProjectControlIcon name="down" />
          </button>
          <button type="button" className={styles.button} onClick={() => setLinkDialogOpen(true)}><ProjectControlIcon name="link" />添加网页</button>
          {uploadMenuOpen && <ActionMenuPopover anchor={uploadTrigger} align="start" label="上传项目资源" onClose={() => setUploadMenuOpen(false)}>
            <button type="button" role="menuitem" onClick={() => { setUploadMenuOpen(false); fileInput.current?.click(); }}><ProjectControlIcon name="upload" /><span>上传文件</span></button>
            <button type="button" role="menuitem" onClick={() => { setUploadMenuOpen(false); setLinkDialogOpen(true); }}><ProjectControlIcon name="link" /><span>添加网页</span></button>
          </ActionMenuPopover>}
          {uploadError && <p className={styles.uploadError} role="alert">{uploadError}</p>}
        </>}>
          {!!(resources.documents.length + resources.links.length) && <ul className={styles.compactList}>
            {resources.documents.map((item) => <li key={item.inputArtifactId}>
              <a href={`/api/input-artifacts/${encodeURIComponent(item.inputArtifactId)}/content?inline=1`} target="_blank" rel="noreferrer" title={item.name}>
                <ProjectResourceIcon kind="file" name={item.name} /><span>{item.name}</span>
              </a>
            </li>)}
            {resources.links.map((item) => <li key={item.linkId}>
              <a href={item.url} target="_blank" rel="noreferrer" title={item.url}>
                <ProjectResourceIcon kind="link" /><span>{item.title || item.url.replace(/^https?:\/\/(www\.)?/, "")}</span>
              </a>
            </li>)}
          </ul>}
        </ProjectCardSection>
        <div className={styles.cardDivider} />
        <ProjectCardSection kind="skills" label="项目技能列表" heading={<>
          <button type="button" className={styles.cardHeading} onClick={() => setDialog("skills")}>
            技能 <ProjectControlIcon name="right" />
          </button>
          <button type="button" ref={skillTrigger} className={styles.cardIconButton} aria-label="添加项目技能" title="添加技能" aria-haspopup="menu" aria-expanded={skillMenuOpen}
            onClick={() => setSkillMenuOpen(value => !value)} onKeyDown={event => { if (event.key === "ArrowDown") { event.preventDefault(); setSkillMenuOpen(true); } }}>
            <ProjectControlIcon name="plus" />
          </button>
          {skillMenuOpen && <ActionMenuPopover anchor={skillTrigger} align="end" label="添加项目技能" onClose={() => setSkillMenuOpen(false)}>
            <button type="button" role="menuitem" onClick={() => { setSkillMenuOpen(false); setDialog("skills"); }}>
              <ProjectResourceIcon kind="skill" /><span>从平台技能添加</span>
            </button>
          </ActionMenuPopover>}
        </>}>
          {!!project.skillReferences?.length && <ul className={styles.compactList}>
            {project.skillReferences.map((ref) => <li key={ref}>
              <button type="button" className={styles.resourceRow} title={skillNames.get(ref) ?? ref} onClick={() => setDialog("skills")}>
                <ProjectResourceIcon kind="skill" name={skillNames.get(ref) ?? ref} /><span>{skillNames.get(ref) ?? ref}</span>
              </button>
            </li>)}
          </ul>}
        </ProjectCardSection>
      </section>
      <section className={styles.contextCard} data-card="knowledge">
        <ProjectCardSection kind="knowledge" label="项目知识库列表" heading={<>
          <button type="button" className={styles.cardHeading} onClick={() => setDialog("knowledge")}>知识库 <ProjectControlIcon name="right" /></button>
          <button type="button" className={styles.cardIconButton} aria-label="关联项目知识库" title="关联知识库" onClick={() => setDialog("knowledge")}><ProjectControlIcon name="plus" /></button>
        </>}>
          {!!project.knowledgeReferences?.length && <ul className={styles.compactList}>
            {project.knowledgeReferences.map((ref) => <li key={ref}>
              <Link href={`/studio/knowledge/${encodeURIComponent(ref)}`} title={knowledgeNames.get(ref) ?? ref}>
                <ProjectResourceIcon kind="knowledge" /><span>{knowledgeNames.get(ref) ?? ref}</span>
              </Link>
            </li>)}
          </ul>}
        </ProjectCardSection>
      </section>
    </aside>
    {linkDialogOpen && <ProjectFilesDialog project={project} mode="manage" initialAction="link" onClose={() => setLinkDialogOpen(false)} onChanged={() => void refreshResources()} />}
  </>;
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
