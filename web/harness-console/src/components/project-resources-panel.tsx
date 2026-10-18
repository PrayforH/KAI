"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  projectClient,
  studioClient,
  type ApiProject,
  type ApiProjectDocument,
  type ApiProjectLink,
  type StudioKnowledgeBase,
  type StudioPlatformSkillPackage,
} from "../lib/studio-client";
import {
  ProjectFilesDialog,
  ProjectKnowledgeDialog,
  ProjectSkillsDialog,
} from "./project-resource-dialogs";
import styles from "./project-resources-panel.module.css";

type PanelDialog = "knowledge" | "files" | "skills" | null;

function ResourceCard({
  title,
  description,
  action,
  count,
  onOpen,
  children,
}: {
  title: string;
  description: string;
  action: string;
  count: number;
  onOpen: () => void;
  children: ReactNode;
}) {
  return (
    <section className={styles.card}>
      <div className={styles.cardHead}>
        <div className={styles.cardTitle}>
          <h2>{title}</h2>
          {count > 0 ? <span className={styles.count}>{count}</span> : null}
        </div>
        <button
          type="button"
          className={styles.expand}
          aria-label={`展开${title}`}
          title={`展开${title}`}
          onClick={onOpen}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="M6 3h7v7M13 3 8 8M4 5h2M4 8v3h3" />
          </svg>
        </button>
      </div>
      <p className={styles.description}>{description}</p>
      {children}
      <button type="button" className={styles.action} onClick={onOpen}>
        <span aria-hidden="true">＋</span>
        {action}
      </button>
    </section>
  );
}

/**
 * Project-scoped context beside a task conversation.
 *
 * The task already carries its own files in the workbench rail; this panel is
 * only the project's shared inputs. Memories stay out of this surface.
 */
export function ProjectResourcesPanel({ projectId }: { projectId: string }) {
  const [project, setProject] = useState<ApiProject | null>(null);
  const [documents, setDocuments] = useState<ApiProjectDocument[]>([]);
  const [links, setLinks] = useState<ApiProjectLink[]>([]);
  const [knowledgeBases, setKnowledgeBases] = useState<StudioKnowledgeBase[]>([]);
  const [packages, setPackages] = useState<StudioPlatformSkillPackage[]>([]);
  const [dialog, setDialog] = useState<PanelDialog>(null);
  const [error, setError] = useState("");

  async function reload() {
    try {
      const [resources, catalog, bases] = await Promise.all([
        projectClient.resources(projectId),
        studioClient
          .listPlatformSkills()
          .catch(() => ({ revision: 0, packages: [] as StudioPlatformSkillPackage[] })),
        studioClient.listKnowledgeBases().catch(() => [] as StudioKnowledgeBase[]),
      ]);
      setProject(resources.project);
      setDocuments(resources.documents);
      setLinks(resources.links ?? []);
      setPackages(catalog.packages);
      setKnowledgeBases(bases);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取项目资源失败");
    }
  }

  useEffect(() => {
    void reload();
  }, [projectId]);

  const knowledge = useMemo(() => {
    const byReference = new Map(knowledgeBases.map((item) => [item.reference, item]));
    return (project?.knowledgeReferences ?? []).map((reference) => ({
      reference,
      base: byReference.get(reference),
    }));
  }, [project?.knowledgeReferences, knowledgeBases]);

  const skills = useMemo(() => {
    const byReference = new Map(
      packages.map((pkg) => [`${pkg.packageId}@${pkg.revision}`, pkg]),
    );
    return (project?.skillReferences ?? []).map((reference) => ({
      reference,
      package: byReference.get(reference),
    }));
  }, [project?.skillReferences, packages]);

  const files = documents.length + links.length;
  const previewFiles = [
    ...documents.slice(0, 3).map((document) => ({
      key: document.inputArtifactId,
      kind: "document" as const,
      label: document.name,
      href: `/api/input-artifacts/${encodeURIComponent(document.inputArtifactId)}/content?inline=1`,
      action: "查看",
    })),
    ...links.slice(0, 3).map((link) => ({
      key: link.linkId,
      kind: "link" as const,
      label: link.title || link.url,
      href: link.url,
      action: "打开",
    })),
  ].slice(0, 4);

  return (
    <section className={styles.panel} aria-label="项目资源">
      {error ? <p className={styles.error}>{error}</p> : null}
      <ResourceCard
        title="知识库"
        description="绑定知识库，让项目里的每个任务都能检索同一批资料。"
        action={knowledge.length ? "配置" : "添加"}
        count={knowledge.length}
        onOpen={() => setDialog("knowledge")}
      >
        {knowledge.length ? (
          <ul className={styles.chips}>
            {knowledge.slice(0, 6).map(({ reference, base }) => (
              <li key={reference} title={reference}>{base?.displayName ?? reference}</li>
            ))}
          </ul>
        ) : <p className={styles.muted}>还没有绑定知识库。</p>}
      </ResourceCard>

      <ResourceCard
        title="文件和资源"
        description="附加文件和网页，供项目的所有任务使用。"
        action="添加"
        count={files}
        onOpen={() => setDialog("files")}
      >
        {previewFiles.length ? (
          <ul className={styles.fileList}>
            {previewFiles.map((item) => (
              <li key={item.key}>
                <span className={styles.fileGlyph} aria-hidden="true">
                  {item.kind === "link" ? "↗" : "▤"}
                </span>
                <span className={styles.fileName} title={item.label}>{item.label}</span>
                <a href={item.href} target="_blank" rel="noreferrer">{item.action}</a>
              </li>
            ))}
            {files > previewFiles.length ? (
              <li className={styles.moreLine}>还有 {files - previewFiles.length} 项</li>
            ) : null}
          </ul>
        ) : <p className={styles.muted}>还没有附加文件或网页。</p>}
      </ResourceCard>

      <ResourceCard
        title="技能"
        description="在相关情况下跨任务复用已经跑通的工作流。"
        action="添加"
        count={skills.length}
        onOpen={() => setDialog("skills")}
      >
        {skills.length ? (
          <ul className={styles.chips}>
            {skills.slice(0, 6).map(({ reference, package: pkg }) => (
              <li key={reference} title={reference}>{pkg?.displayName ?? reference}</li>
            ))}
          </ul>
        ) : <p className={styles.muted}>还没有项目技能。</p>}
      </ResourceCard>

      {project && dialog === "knowledge" ? (
        <ProjectKnowledgeDialog
          project={project}
          onClose={() => setDialog(null)}
          onChanged={() => void reload()}
        />
      ) : null}
      {project && dialog === "files" ? (
        <ProjectFilesDialog
          project={project}
          mode="manage"
          onClose={() => setDialog(null)}
          onChanged={() => void reload()}
        />
      ) : null}
      {project && dialog === "skills" ? (
        <ProjectSkillsDialog
          project={project}
          packages={packages}
          onClose={() => setDialog(null)}
          onChanged={() => void reload()}
        />
      ) : null}
    </section>
  );
}
