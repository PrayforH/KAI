"use client";

import { useEffect, useState } from "react";
import {
  projectClient,
  studioClient,
  type ApiProject,
  type ApiProjectDocument,
  type ApiProjectLink,
  type StudioPlatformSkillPackage,
} from "../lib/studio-client";
import {
  ProjectFilesDialog,
  ProjectKnowledgeDialog,
  ProjectSkillsDialog,
} from "./project-resource-dialogs";
import styles from "./project-resources-panel.module.css";

type PanelDialog = "knowledge" | "files" | "skills" | null;

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
  const [packages, setPackages] = useState<StudioPlatformSkillPackage[]>([]);
  const [dialog, setDialog] = useState<PanelDialog>(null);
  const [error, setError] = useState("");

  async function reload() {
    try {
      const [resources, catalog] = await Promise.all([
        projectClient.resources(projectId),
        studioClient
          .listPlatformSkills()
          .catch(() => ({ revision: 0, packages: [] as StudioPlatformSkillPackage[] })),
      ]);
      setProject(resources.project);
      setDocuments(resources.documents);
      setLinks(resources.links ?? []);
      setPackages(catalog.packages);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取项目资源失败");
    }
  }

  useEffect(() => {
    void reload();
  }, [projectId]);

  const knowledge = project?.knowledgeReferences?.length ?? 0;
  const skills = project?.skillReferences?.length ?? 0;
  const files = documents.length + links.length;

  return (
    <section className={styles.panel} aria-label="项目资源">
      <header className={styles.head}>
        <h2>{project?.name ?? "项目资源"}</h2>
        <p>项目资源会随该项目下的每个任务生效。</p>
      </header>
      {error ? <p className={styles.error}>{error}</p> : null}
      <button
        type="button"
        className={styles.row}
        onClick={() => setDialog("knowledge")}
      >
        <span className={styles.rowIcon} aria-hidden="true">◎</span>
        <span className={styles.rowCopy}>
          <strong>知识库</strong>
          <em>{knowledge ? `已绑定 ${knowledge} 个` : "还没有绑定知识库"}</em>
        </span>
        <span className={styles.count}>{knowledge}</span>
      </button>
      <button
        type="button"
        className={styles.row}
        onClick={() => setDialog("files")}
      >
        <span className={styles.rowIcon} aria-hidden="true">▤</span>
        <span className={styles.rowCopy}>
          <strong>文件和资源</strong>
          <em>{files ? `${documents.length} 个文件 · ${links.length} 个网页` : "还没有附加文件或网页"}</em>
        </span>
        <span className={styles.count}>{files}</span>
      </button>
      <button
        type="button"
        className={styles.row}
        onClick={() => setDialog("skills")}
      >
        <span className={styles.rowIcon} aria-hidden="true">✧</span>
        <span className={styles.rowCopy}>
          <strong>技能</strong>
          <em>{skills ? `已选择 ${skills} 个` : "还没有项目技能"}</em>
        </span>
        <span className={styles.count}>{skills}</span>
      </button>

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
