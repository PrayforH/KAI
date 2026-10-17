"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "./auth-provider";
import { ProjectSettingsDialog } from "./project-settings-dialog";
import { formatTaskAge } from "../lib/task-list-age";
import { loadTasks, type TaskSummary } from "../lib/task-history";
import { createProjectTask } from "../lib/project-task";
import { loadTaskAgentCatalog, type TaskAgent } from "../lib/task-agent-catalog";
import {
  projectClient,
  studioClient,
  type ApiProject,
  type ApiProjectDocument,
  type ApiProjectMemory,
  type StudioPlatformSkillPackage,
} from "../lib/studio-client";
import styles from "./project-home.module.css";

const STATUS_LABELS: Record<string, string> = {
  idle: "新任务",
  queued: "排队中",
  provisioning: "准备中",
  running: "运行中",
  waiting_approval: "待审批",
  cancelling: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "处理错误",
  rejected: "已拒绝",
  timed_out: "已超时",
  archived: "已归档",
};

function statusTone(status: string) {
  if (["queued", "provisioning", "running", "waiting_approval", "cancelling"].includes(status)) {
    return "active";
  }
  if (["failed", "timed_out", "rejected"].includes(status)) return "error";
  if (status === "succeeded") return "success";
  return "neutral";
}

export function ProjectHome({ projectId }: { projectId: string }) {
  const router = useRouter();
  const { user } = useAuth();
  const [project, setProject] = useState<ApiProject | null>(null);
  const [memories, setMemories] = useState<ApiProjectMemory[]>([]);
  const [documents, setDocuments] = useState<ApiProjectDocument[]>([]);
  const [skillCatalog, setSkillCatalog] = useState<StudioPlatformSkillPackage[]>([]);
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [defaultAgent, setDefaultAgent] = useState<TaskAgent | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState("");

  const reload = useCallback(async () => {
    setError("");
    try {
      const [resources, skillCatalogValue, taskList] = await Promise.all([
        projectClient.resources(projectId),
        studioClient.listPlatformSkills().catch(() => ({ revision: 0, packages: [] })),
        loadTasks().catch(() => []),
      ]);
      setProject(resources.project);
      setMemories(resources.memories);
      setDocuments(resources.documents);
      setSkillCatalog(skillCatalogValue.packages);
      setTasks(taskList.filter((task) => task.project_id === projectId && !task.archived_at));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取项目失败，请稍后重试。");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const refresh = () => { void reload(); };
    window.addEventListener("harness:task-list-changed", refresh);
    return () => window.removeEventListener("harness:task-list-changed", refresh);
  }, [reload]);

  useEffect(() => {
    let active = true;
    loadTaskAgentCatalog(user.user_id, false)
      .then((catalog) => { if (active) setDefaultAgent(catalog.defaultAgent); })
      .catch(() => { /* The New task button explains the failure. */ });
    return () => { active = false; };
  }, [user.user_id]);

  const skills = useMemo(() => {
    const byReference = new Map(
      skillCatalog.map((pkg) => [`${pkg.packageId}@${pkg.revision}`, pkg]),
    );
    return (project?.skillReferences ?? []).map((reference) => ({
      reference,
      package: byReference.get(reference),
    }));
  }, [project?.skillReferences, skillCatalog]);

  async function startTask() {
    if (creating) return;
    setCreating(true);
    setError("");
    try {
      const task = await createProjectTask(projectId, defaultAgent);
      router.push(`/?thread=${encodeURIComponent(task.thread_id)}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "新建任务失败，请稍后重试。");
      setCreating(false);
    }
  }

  if (loading) {
    return (
      <div className={styles.page} aria-busy="true">
        <div className={styles.loading} role="status">正在读取项目…</div>
      </div>
    );
  }

  if (!project) {
    return (
      <div className={styles.page}>
        <div className={styles.emptyPage}>
          <h1>项目不可用</h1>
          <p>{error || "这个项目可能已被移除，或者你没有访问权限。"}</p>
          <Link href="/studio/agents">返回智能体</Link>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <div className={styles.heading}>
          <span className={styles.eyebrow}>项目</span>
          <h1>{project.name}</h1>
          <p>
            {tasks.length} 个任务 · {documents.length} 个文件 · {memories.length} 条记忆 · {skills.length} 个技能
          </p>
        </div>
        <div className={styles.actions}>
          <button type="button" className={styles.secondary} onClick={() => setSettingsOpen(true)}>
            项目设置
          </button>
          <button type="button" className={styles.primary} disabled={creating} onClick={() => void startTask()}>
            {creating ? "正在创建…" : "新建任务"}
          </button>
        </div>
      </header>

      {error && <p className={styles.error} role="alert">{error}</p>}

      <div className={styles.layout}>
        <section className={styles.tasks} aria-label="项目任务">
          <div className={styles.sectionHead}>
            <h2>任务</h2>
            <span>{tasks.length}</span>
          </div>
          {tasks.length ? (
            <ul className={styles.taskList}>
              {tasks.map((task) => (
                <li key={task.thread_id}>
                  <Link className={styles.task} href={`/?thread=${encodeURIComponent(task.thread_id)}`}>
                    <span className={styles.taskStatus} data-tone={statusTone(task.status)} aria-hidden="true" />
                    <span className={styles.taskCopy}>
                      <strong>{task.title}</strong>
                      <small>
                        {STATUS_LABELS[task.status] ?? task.status}
                        {" · "}
                        {formatTaskAge(task.updated_at)}
                        {task.pending_approval ? " · 等待你的确认" : ""}
                      </small>
                    </span>
                    <span className={styles.taskArrow} aria-hidden="true">→</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <div className={styles.emptyState}>
              <p>这个项目还没有任务。</p>
              <button type="button" disabled={creating} onClick={() => void startTask()}>
                在此项目中开始
              </button>
            </div>
          )}
        </section>

        <aside className={styles.rail} aria-label="项目资源">
          <section className={styles.card}>
            <div className={styles.cardHead}>
              <h2>项目说明</h2>
            </div>
            {project.instructions?.trim() ? (
              <p className={styles.instructions}>{project.instructions}</p>
            ) : (
              <p className={styles.muted}>还没有项目说明。项目设置里可以写目标、术语和输出规范。</p>
            )}
          </section>

          <section className={styles.card}>
            <div className={styles.cardHead}>
              <h2>项目文件</h2>
              <button type="button" onClick={() => setSettingsOpen(true)}>管理</button>
            </div>
            {documents.length ? (
              <ul className={styles.resourceList}>
                {documents.map((document) => (
                  <li key={document.inputArtifactId}>
                    <span className={styles.fileIcon} aria-hidden="true">▤</span>
                    <span className={styles.resourceName} title={document.name}>{document.name}</span>
                    <a
                      className={styles.resourceAction}
                      href={`/api/input-artifacts/${encodeURIComponent(document.inputArtifactId)}/content?inline=1`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      查看
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className={styles.muted}>还没有项目文件。可以附加资料，让这个项目的每个新任务都能使用。</p>
            )}
          </section>

          <section className={styles.card}>
            <div className={styles.cardHead}>
              <h2>项目技能</h2>
              <button type="button" onClick={() => setSettingsOpen(true)}>管理</button>
            </div>
            {skills.length ? (
              <ul className={styles.chips}>
                {skills.map(({ reference, package: pkg }) => (
                  <li key={reference} title={reference}>{pkg?.displayName ?? reference}</li>
                ))}
              </ul>
            ) : (
              <p className={styles.muted}>还没有项目技能。选中的平台技能会带到每个新任务。</p>
            )}
          </section>

          <section className={styles.card}>
            <div className={styles.cardHead}>
              <h2>项目记忆</h2>
              <button type="button" onClick={() => setSettingsOpen(true)}>管理</button>
            </div>
            {memories.length ? (
              <ul className={styles.memoryList}>
                {memories.map((memory) => <li key={memory.memoryId}>{memory.content}</li>)}
              </ul>
            ) : (
              <p className={styles.muted}>还没有项目记忆。长期有效的事实和决定可以写在这里。</p>
            )}
          </section>
        </aside>
      </div>

      {settingsOpen && (
        <ProjectSettingsDialog
          project={project}
          onClose={() => { setSettingsOpen(false); void reload(); }}
          onChanged={() => { void reload(); }}
          onRemoved={() => router.push("/")}
        />
      )}
    </div>
  );
}
