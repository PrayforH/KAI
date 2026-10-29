"use client";

import Link from "next/link";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
} from "react";
import { ProjectModal } from "./project-modal";
export { ProjectModal } from "./project-modal";
import { ProjectWebSearchDialog } from "./project-web-search-dialog";
import {
  projectClient,
  studioClient,
  type ApiProject,
  type ApiProjectDocument,
  type ApiProjectLink,
  type ApiProjectSkill,
  type StudioKnowledgeBase,
  type StudioPlatformSkillPackage,
} from "../lib/studio-client";
import styles from "./project-resource-dialogs.module.css";

type Notice = { tone: "error" | "info"; text: string } | null;

type ProjectArtifact = {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes: number | null;
  run_id: string;
  thread_id: string;
  thread_title: string;
  created_at: string;
};

function sourceLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function formatSize(bytes: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function matches(query: string, ...values: Array<string | null | undefined>): boolean {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return values.some((value) => value?.toLowerCase().includes(needle));
}

export async function uploadProjectDocument(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/input-artifacts", { method: "POST", body: form });
  const payload = (await response.json().catch(() => null)) as
    | Record<string, unknown>
    | null;
  if (!response.ok) {
    const detail = (payload?.detail ?? payload?.error) as
      | Record<string, unknown>
      | undefined;
    throw new Error(
      typeof detail?.message === "string"
        ? detail.message
        : `上传失败（HTTP ${response.status}）`,
    );
  }
  const id = payload?.input_artifact_id;
  if (typeof id !== "string" || !id) {
    throw new Error("上传成功但没有返回附件标识，请重试。");
  }
  return id;
}

function SearchField({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <label className={styles.search}>
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <circle cx="7" cy="7" r="4.2" />
        <path d="m10.2 10.2 3 3" />
      </svg>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
      />
    </label>
  );
}

function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className={styles.empty}>
      <span className={styles.emptyIcon} aria-hidden="true">
        <svg viewBox="0 0 48 48">
          <path d="M12 7h17l7 7v27H12Z" />
          <path d="M29 7v7h7" />
        </svg>
      </span>
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ProjectKnowledgeDialog({
  project,
  onClose,
  onChanged,
}: {
  project: ApiProject;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [bases, setBases] = useState<StudioKnowledgeBase[]>([]);
  const [selected, setSelected] = useState<string[]>(project.knowledgeReferences ?? []);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    let active = true;
    studioClient
      .listKnowledgeBases()
      .then((items) => {
        if (active) setBases(items);
      })
      .catch((cause: unknown) => {
        if (active) {
          setNotice({
            tone: "error",
            text: cause instanceof Error ? cause.message : "读取知识库失败",
          });
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const visible = useMemo(
    () =>
      bases.filter((item) =>
        matches(query, item.displayName, item.description, item.reference),
      ),
    [bases, query],
  );

  function toggle(reference: string) {
    setSelected((current) =>
      current.includes(reference)
        ? current.filter((item) => item !== reference)
        : [...current, reference],
    );
    setNotice(null);
  }

  async function save() {
    setSaving(true);
    setNotice(null);
    try {
      await projectClient.update(project.projectId, { knowledgeReferences: selected });
      onChanged();
      onClose();
    } catch (cause) {
      setNotice({
        tone: "error",
        text: cause instanceof Error ? cause.message : "保存失败，请重试。",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <ProjectModal
      title="知识库"
      subtitle="选中的知识库会随项目应用到每个任务；任务内仍可临时追加。"
      onClose={onClose}
      toolbar={
        <SearchField value={query} onChange={setQuery} placeholder="搜索知识库" />
      }
      footer={
        <>
          <span className={styles.footerNote}>已选 {selected.length} 个知识库</span>
          <div className={styles.footerActions}>
            <button type="button" className={styles.secondary} onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className={styles.primary}
              disabled={saving || loading}
              onClick={() => void save()}
            >
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        </>
      }
    >
      {notice?.tone === "error" ? <p className={styles.error}>{notice.text}</p> : null}
      {loading ? <p className={styles.loading}>正在读取知识库…</p> : null}
      {!loading && bases.length === 0 ? (
        <EmptyState
          title="还没有知识库"
          description="先创建知识库并上传资料，再把它们绑定到项目。"
          action={
            <Link className={styles.primary} href="/studio/knowledge" onClick={onClose}>
              前往知识库
            </Link>
          }
        />
      ) : null}
      {!loading && bases.length > 0 && visible.length === 0 ? (
        <p className={styles.loading}>没有匹配的知识库。</p>
      ) : null}
      <ul className={styles.list}>
        {visible.map((item) => {
          const checked = selected.includes(item.reference);
          return (
            <li key={item.reference}>
              <button
                type="button"
                className={styles.row}
                data-selected={checked}
                onClick={() => toggle(item.reference)}
              >
                <span className={styles.check} aria-hidden="true">
                  {checked ? "✓" : ""}
                </span>
                <span className={styles.rowCopy}>
                  <strong>{item.displayName}</strong>
                  <span>{item.description || item.reference}</span>
                  <em>
                    {item.documentCount} 个文档 · {item.engine === "weknora" ? "WeKnora" : "内置"}
                  </em>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </ProjectModal>
  );
}

export function ProjectSkillsDialog({
  project,
  packages,
  onClose,
  onChanged,
}: {
  project: ApiProject;
  packages: StudioPlatformSkillPackage[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [selected, setSelected] = useState<string[]>(project.skillReferences ?? []);
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  const [localPending, setLocalPending] = useState("");
  const localSkills = (project.localSkills ?? []).filter(item => matches(query, item.displayName, item.skill.name, item.skill.description));
  async function changeLocal(item: ApiProjectSkill, remove = false) {
    setLocalPending(item.skillId); setNotice(null);
    try {
      if (remove) await projectClient.deleteSkill(project.projectId, item.skillId);
      else await projectClient.setSkillEnabled(project.projectId, item, !item.enabled);
      onChanged();
      window.dispatchEvent(new CustomEvent("harness:project-results-changed", {detail: {projectId:project.projectId}}));
    } catch (cause) { setNotice({tone:"error", text:cause instanceof Error ? cause.message : "修改项目技能失败"}); }
    finally { setLocalPending(""); }
  }
  const visible = useMemo(
    () =>
      packages.filter((item) =>
        matches(
          query,
          item.displayName,
          item.summary,
          item.packageId,
          ...item.tags,
          sourceLabel(item.sourceUrl),
        ),
      ),
    [packages, query],
  );

  function reference(pkg: StudioPlatformSkillPackage) {
    return `${pkg.packageId}@${pkg.revision}`;
  }

  function toggle(pkg: StudioPlatformSkillPackage) {
    const value = reference(pkg);
    setSelected((current) =>
      current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    );
    setNotice(null);
  }

  async function save() {
    setSaving(true);
    setNotice(null);
    try {
      await projectClient.update(project.projectId, { skillReferences: selected });
      onChanged();
      onClose();
    } catch (cause) {
      setNotice({
        tone: "error",
        text: cause instanceof Error ? cause.message : "保存失败，请重试。",
      });
    } finally {
      setSaving(false);
    }
  }

  return (
    <ProjectModal
      title="技能"
      subtitle="仅影响当前项目；智能体自带技能随其发布版本管理。"
      onClose={onClose}
      toolbar={
        <SearchField value={query} onChange={setQuery} placeholder="搜索技能、标签或来源" />
      }
      footer={
        <>
          <span className={styles.footerNote}>已选 {selected.length} 个技能</span>
          <div className={styles.footerActions}>
            <button type="button" className={styles.secondary} onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className={styles.primary}
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        </>
      }
    >
      {notice?.tone === "error" ? <p className={styles.error}>{notice.text}</p> : null}
      {visible.length === 0 && localSkills.length === 0 ? <p className={styles.loading}>没有匹配的技能。</p> : null}
      {localSkills.length > 0 && <section className={styles.section}><h3>项目技能</h3><ul className={styles.list}>
        {localSkills.map(item => <li key={item.skillId} className={styles.localSkill}>
          <div><strong>{item.displayName}</strong><small>{item.enabled ? "已启用" : "草稿"} · v{item.revision}</small></div>
          <p>{item.skill.description}</p>
          <details><summary>查看步骤和文件</summary><pre>{item.skill.instructions}</pre>{item.skill.files?.map(file=><details key={file.path}><summary>{file.path}</summary><pre>{file.content ?? "二进制附件"}</pre></details>)}</details>
          <div className={styles.localActions}>
            <button type="button" className={styles.secondary} disabled={Boolean(localPending)} aria-label={`${item.enabled ? "停用" : "启用"} ${item.displayName}`} onClick={()=>void changeLocal(item)}>{item.enabled ? "停用" : "启用"}</button>
            {!item.enabled && <button type="button" className={styles.secondary} disabled={Boolean(localPending)} onClick={()=>void changeLocal(item,true)}>删除草稿</button>}
          </div>
        </li>)}
      </ul></section>}
      {visible.length > 0 && localSkills.length > 0 && <h3>平台技能</h3>}
      <ul className={styles.list}>
        {visible.map((pkg) => {
          const value = reference(pkg);
          const checked = selected.includes(value);
          return (
            <li key={value}>
              <button
                type="button"
                className={styles.row}
                data-selected={checked}
                onClick={() => toggle(pkg)}
              >
                <span className={styles.check} aria-hidden="true">
                  {checked ? "✓" : ""}
                </span>
                <span className={styles.rowCopy}>
                  <strong>{pkg.displayName}</strong>
                  <span>{pkg.summary}</span>
                  <em>
                    来源 {sourceLabel(pkg.sourceUrl)} · {pkg.license} · {value}
                  </em>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </ProjectModal>
  );
}

export function ProjectFilesDialog({
  project,
  mode,
  initialAction,
  onClose,
  onChanged,
}: {
  project: ApiProject;
  mode: "manage" | "browse";
  initialAction?: "link";
  onClose: () => void;
  onChanged?: () => void;
}) {
  const [documents, setDocuments] = useState<ApiProjectDocument[]>([]);
  const [links, setLinks] = useState<ApiProjectLink[]>([]);
  const [artifacts, setArtifacts] = useState<ProjectArtifact[]>([]);
  const [query, setQuery] = useState("");
  const [uploading, setUploading] = useState(false);
  const [webSearchOpen, setWebSearchOpen] = useState(initialAction === "link");
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<Notice>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function reload() {
    const resources = await projectClient.resources(project.projectId);
    setDocuments(resources.documents);
    setLinks(resources.links ?? []);
  }

  useEffect(() => {
    let active = true;
    const requests: Array<Promise<void>> = [
      projectClient.resources(project.projectId).then((resources) => {
        if (!active) return;
        setDocuments(resources.documents);
        setLinks(resources.links ?? []);
      }),
    ];
    if (mode === "browse") {
      requests.push(
        fetch(`/api/harness/artifacts?project_id=${encodeURIComponent(project.projectId)}`, {
          cache: "no-store",
        }).then(async (response) => {
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const items = (await response.json()) as ProjectArtifact[];
          if (active) setArtifacts(items);
        }),
      );
    }
    Promise.all(requests)
      .catch((cause: unknown) => {
        if (active) {
          setNotice({
            tone: "error",
            text: cause instanceof Error ? cause.message : "读取项目文件失败",
          });
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [mode, project.projectId]);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true);
    setNotice(null);
    try {
      const inputArtifactId = await uploadProjectDocument(file);
      await projectClient.attachDocument(project.projectId, inputArtifactId);
      await reload();
      onChanged?.();
    } catch (cause) {
      setNotice({
        tone: "error",
        text: cause instanceof Error ? cause.message : "上传失败，请重试。",
      });
    } finally {
      setUploading(false);
    }
  }

  async function removeDocument(document: ApiProjectDocument) {
    setNotice(null);
    try {
      await projectClient.detachDocument(project.projectId, document.inputArtifactId);
      await reload();
      onChanged?.();
    } catch (cause) {
      setNotice({
        tone: "error",
        text: cause instanceof Error ? cause.message : "移除失败，请重试。",
      });
    }
  }

  async function removeLink(link: ApiProjectLink) {
    setNotice(null);
    try {
      await projectClient.deleteLink(project.projectId, link.linkId);
      await reload();
      onChanged?.();
    } catch (cause) {
      setNotice({
        tone: "error",
        text: cause instanceof Error ? cause.message : "移除失败，请重试。",
      });
    }
  }

  const visibleDocuments = documents.filter((item) => matches(query, item.name));
  const visibleLinks = links.filter((item) => matches(query, item.title, item.url));
  const visibleArtifacts = artifacts.filter((item) =>
    matches(query, item.name, item.thread_title),
  );
  const totalVisible =
    visibleDocuments.length + visibleLinks.length + visibleArtifacts.length;

  return (
    <ProjectModal
      title={mode === "browse" ? "项目文件" : "文件和资源"}
      subtitle={
        mode === "browse"
          ? "项目资料与该项目下任务产出的文件，按任务归集。"
          : "附加文件和网页供项目中的所有任务使用。"
      }
      onClose={onClose}
      toolbar={
        <>
          <SearchField
            value={query}
            onChange={setQuery}
            placeholder={mode === "browse" ? "搜索项目文件" : "搜索文件和资源"}
          />
          {mode === "manage" ? (
            <div className={styles.addGroup}>
              <input
                ref={fileRef}
                type="file"
                className={styles.hiddenFile}
                onChange={(event) => void upload(event)}
              />
              <button
                type="button"
                className={styles.primary}
                disabled={uploading}
                onClick={() => fileRef.current?.click()}
              >
                {uploading ? "上传中…" : "＋ 上传文件"}
              </button>
              <button
                type="button"
                className={styles.secondary}
                onClick={() => setWebSearchOpen(true)}
              >
                搜索网页
              </button>
            </div>
          ) : null}
        </>
      }
    >
      {notice?.tone === "error" ? <p className={styles.error}>{notice.text}</p> : null}
      {webSearchOpen && <ProjectWebSearchDialog project={project} onClose={() => setWebSearchOpen(false)} onChanged={() => { void reload(); onChanged?.(); }} />}
      {loading ? <p className={styles.loading}>正在读取项目文件…</p> : null}
      {!loading && totalVisible === 0 ? (
        <EmptyState
          title={query ? "没有匹配的文件" : "还没有项目文件"}
          description={
            mode === "browse"
              ? "项目资料和任务产物会在这里汇总。"
              : "附加文件和网页供项目中的所有任务使用。"
          }
          action={
            mode === "manage" ? (
              <button
                type="button"
                className={styles.primary}
                onClick={() => fileRef.current?.click()}
              >
                ＋ 添加
              </button>
            ) : undefined
          }
        />
      ) : null}

      {visibleDocuments.length > 0 ? (
        <section className={styles.section}>
          <h3>项目资料</h3>
          <ul className={styles.fileRows}>
            {visibleDocuments.map((document) => (
              <li key={document.inputArtifactId}>
                <span className={styles.fileIcon} aria-hidden="true">▤</span>
                <span className={styles.fileCopy}>
                  <strong>{document.name}</strong>
                  <em>
                    {formatSize(document.sizeBytes)}
                    {document.mediaType ? ` · ${document.mediaType}` : ""}
                  </em>
                </span>
                <a
                  href={`/api/input-artifacts/${encodeURIComponent(document.inputArtifactId)}/content?inline=1`}
                  target="_blank"
                  rel="noreferrer"
                >
                  查看
                </a>
                {mode === "manage" ? (
                  <button
                    type="button"
                    className={styles.remove}
                    aria-label={`移除 ${document.name}`}
                    onClick={() => void removeDocument(document)}
                  >
                    ×
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {visibleLinks.length > 0 ? (
        <section className={styles.section}>
          <h3>网页</h3>
          <ul className={styles.fileRows}>
            {visibleLinks.map((link) => (
              <li key={link.linkId}>
                <span className={styles.fileIcon} aria-hidden="true">↗</span>
                <span className={styles.fileCopy}>
                  <strong>{link.title || link.url}</strong>
                  <em>{link.url}</em>
                </span>
                <a href={link.url} target="_blank" rel="noreferrer">
                  打开
                </a>
                {mode === "manage" ? (
                  <button
                    type="button"
                    className={styles.remove}
                    aria-label={`移除 ${link.title || link.url}`}
                    onClick={() => void removeLink(link)}
                  >
                    ×
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {visibleArtifacts.length > 0 ? (
        <section className={styles.section}>
          <h3>任务产物</h3>
          <ul className={styles.fileRows}>
            {visibleArtifacts.map((artifact) => (
              <li key={artifact.artifact_id}>
                <span className={styles.fileIcon} aria-hidden="true">▦</span>
                <span className={styles.fileCopy}>
                  <strong>{artifact.name}</strong>
                  <em>
                    {artifact.thread_title} · {formatTime(artifact.created_at)}
                    {formatSize(artifact.size_bytes)
                      ? ` · ${formatSize(artifact.size_bytes)}`
                      : ""}
                  </em>
                </span>
                <a
                  href={`/api/harness/artifacts/${encodeURIComponent(artifact.artifact_id)}/content`}
                  target="_blank"
                  rel="noreferrer"
                >
                  下载
                </a>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </ProjectModal>
  );
}
