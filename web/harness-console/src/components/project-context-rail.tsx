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
  const { project, resources, setDialog, chooseTab } = space;
  if (!project || !resources) return null;
  const rows = [
    { key: "instructions", label: "指令", detail: project.instructions?.trim() || "添加项目背景、要求和交付格式" },
    { key: "files", label: "文件和资源", detail: `${resources.documents.length} 个文件 · ${resources.links.length} 个链接` },
    { key: "knowledge", label: "知识库", detail: `已关联 ${project.knowledgeReferences?.length ?? 0} 个知识库` },
    { key: "connectors", label: "连接器", detail: `已关联 ${project.mcpReferences?.length ?? 0} 个 MCP` },
    { key: "skills", label: "技能", detail: `已关联 ${project.skillReferences?.length ?? 0} 个技能` },
  ] as const;
  return (
    <div className={styles.configuration}>
      <p className={styles.muted}>这里的配置供本项目任务共同使用。</p>
      {rows.map((row) => (
        <button key={row.key} className={styles.configurationRow} onClick={() => setDialog(row.key)}>
          <span><strong>{row.label}</strong><small>{row.detail}</small></span><span aria-hidden="true">›</span>
        </button>
      ))}
      <button className={styles.configurationRow} onClick={() => chooseTab("tasks")}>
        <span><strong>全部项目任务</strong><small>查找历史任务与已归档任务</small></span><span aria-hidden="true">›</span>
      </button>
      <p className={styles.boundaryNote}>能力定义和发布在智能体空间维护；这里管理项目所需的关联。</p>
    </div>
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
