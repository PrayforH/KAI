"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  projectClient,
  studioClient,
  type StudioCapabilities,
} from "../lib/studio-client";
import type { ProjectSpaceState } from "./use-project-space";
import { ProjectModal } from "./project-resource-dialogs";
import { errorText } from "./project-space-shared";
import styles from "./project-home.module.css";

export function ProjectContextRail({ space }: { space: ProjectSpaceState }) {
  const {
    project,
    resources,
    setDialog,
    chooseTab,
    savedResults,
    savedResultError,
    knowledgeNames,
    skillNames,
  } = space;
  if (!project || !resources) return null;
  return (
    <aside className={styles.contextRail} aria-label="项目配置与成果">
      <section className={styles.contextCard}>
        <button
          className={styles.cardHeading}
          onClick={() => setDialog("instructions")}
        >
          指令 <span aria-hidden="true">›</span>
        </button>
        <p className={styles.muted}>适用于本项目的所有任务。</p>
        {project.instructions?.trim() ? (
          <p className={styles.instructionPreview}>{project.instructions}</p>
        ) : null}
        <button
          className={styles.button}
          onClick={() => setDialog("instructions")}
        >
          {project.instructions?.trim() ? "编辑指令" : "＋ 添加指令"}
        </button>
        <div className={styles.cardDivider} />
        <div className={styles.sectionHead}>
          <h2>
            连接器 <small className={styles.count}>MCP</small>
          </h2>
          <button
            className={styles.textLink}
            onClick={() => setDialog("connectors")}
          >
            ＋ 添加
          </button>
        </div>
        <p className={styles.muted}>
          {project.mcpReferences?.length
            ? `已关联 ${project.mcpReferences.length} 个连接器`
            : "连接外部工具与数据服务。"}
        </p>
        {!!project.mcpReferences?.length && (
          <button
            className={styles.textLink}
            onClick={() => setDialog("connectors")}
          >
            查看与管理 ›
          </button>
        )}
      </section>
      <section className={styles.contextCard}>
        <button
          className={styles.cardHeading}
          onClick={() => setDialog("files")}
        >
          文件和资源 <span aria-hidden="true">›</span>
        </button>
        <p className={styles.muted}>供本项目的所有任务使用。</p>
        {!!(resources.documents.length + resources.links.length) && (
          <ul className={styles.compactList}>
            {resources.documents.slice(0, 2).map((item) => (
              <li key={item.inputArtifactId}>
                <a
                  href={`/api/input-artifacts/${encodeURIComponent(item.inputArtifactId)}/content?inline=1`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {item.name}
                </a>
              </li>
            ))}
            {resources.links.slice(0, 1).map((item) => (
              <li key={item.linkId}>
                <a href={item.url} target="_blank" rel="noreferrer">
                  {item.title || item.url}
                </a>
              </li>
            ))}
          </ul>
        )}
        <button className={styles.button} onClick={() => setDialog("files")}>
          {resources.documents.length + resources.links.length
            ? "管理文件与链接"
            : "＋ 上传文件或添加链接"}
        </button>
        <div className={styles.cardDivider} />
        <div className={styles.sectionHead}>
          <h2>知识库</h2>
          <button
            className={styles.textLink}
            onClick={() => setDialog("knowledge")}
          >
            ＋ 关联
          </button>
        </div>
        {project.knowledgeReferences?.length ? (
          <ul className={styles.compactList}>
            {project.knowledgeReferences.slice(0, 3).map((ref) => (
              <li key={ref}>
                <Link href={`/studio/knowledge/${encodeURIComponent(ref)}`}>
                  {knowledgeNames.get(ref) ?? ref}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.muted}>关联已有知识库，供任务检索。</p>
        )}
        {(project.knowledgeReferences?.length ?? 0) > 3 && (
          <button
            className={styles.textLink}
            onClick={() => setDialog("knowledge")}
          >
            查看全部 {project.knowledgeReferences!.length} 个
          </button>
        )}
      </section>
      <section className={styles.contextCard}>
        <button
          className={styles.cardHeading}
          onClick={() => setDialog("skills")}
        >
          技能 <span aria-hidden="true">›</span>
        </button>
        <p className={styles.muted}>为项目任务补充可复用的工作流程。</p>
        {!!project.skillReferences?.length && (
          <ul className={styles.compactList}>
            {project.skillReferences.slice(0, 2).map((ref) => (
              <li key={ref}>{skillNames.get(ref) ?? ref}</li>
            ))}
          </ul>
        )}
        <button className={styles.button} onClick={() => setDialog("skills")}>
          {project.skillReferences?.length ? "管理技能" : "＋ 添加技能"}
        </button>
      </section>
      <section className={styles.contextCard}>
        <div className={styles.sectionHead}>
          <h2>
            项目成果{" "}
            <small className={styles.count}>{savedResults.length || ""}</small>
          </h2>
          <button
            className={styles.textLink}
            onClick={() => chooseTab("results")}
          >
            查看全部 ›
          </button>
        </div>
        {savedResultError ? (
          <p role="alert" className={styles.muted}>
            {savedResultError}
          </p>
        ) : savedResults.length ? (
          <ul className={styles.resultPreviews}>
            {savedResults.slice(0, 3).map((result) => (
              <li key={result.resultId}>
                <button
                  className={styles.resultPreview}
                  onClick={() => chooseTab("results")}
                >
                  <strong>{result.name}</strong>
                  <small>
                    v{result.version} ·{" "}
                    {result.finalizedAt ? "已定稿" : "已保存"}
                  </small>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.muted}>从任务中保存交付物，在这里集中查看。</p>
        )}
        {!!savedResults.length && (
          <p className={styles.muted}>保留文件快照、来源与版本。</p>
        )}
      </section>
    </aside>
  );
}

export function ProjectConnectorsDialog({
  space,
}: {
  space: ProjectSpaceState;
}) {
  const { project, setDialog, refreshResources } = space;
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
            await refreshResources();
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
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, item.reference]
                        : current.filter((ref) => ref !== item.reference),
                    )
                  }
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
