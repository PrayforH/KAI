"use client";

import { useInternalAgentsPreference } from "../../lib/interface-preferences";
import { isAgentVisible } from "../../lib/agent-visibility";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth-provider";
import {
  studioClient,
  apiDraftToStudioDraft,
  type StudioPlatformSkillPackage,
} from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import { useDialogFocus } from "../../lib/use-dialog-focus";
import {
  DEFAULT_SKILL_CREATOR,
  SKILL_SCOPE_LABELS,
  skillCreatorHref,
  type SkillInstallScope,
} from "../../lib/skill-creator-launch";
import styles from "./skills-catalog-page.module.css";

interface CatalogSkill {
  key: string;
  name: string;
  displayName: string;
  description: string;
  instructions: string;
  scope: SkillInstallScope;
  files: Array<{ path: string; binary?: boolean; sizeBytes?: number | null }>;
  fileCount: number;
  agents: Array<{ draftId: string; label: string }>;
  package: StudioPlatformSkillPackage | null;
}

const DISABLED_SKILLS_STORAGE_KEY = "harness-skill-catalog-disabled:v1";

export function SkillsCatalogPage() {
  const { membership } = useAuth();
  const [showInternalAgents] = useInternalAgentsPreference();
  const canManage = membership.role !== "viewer";
  // Mounting reviewed platform Skills requires the backend catalog-admin
  // permission (owner/admin); keep the UI gate aligned to avoid 403s.
  const canManageCatalog = membership.role === "owner" || membership.role === "admin";
  const [skills, setSkills] = useState<CatalogSkill[]>([]);
  const [drafts, setDrafts] = useState<StudioDraft[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scopeFilter, setScopeFilter] = useState<"all" | SkillInstallScope>("all");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [targetDraftId, setTargetDraftId] = useState("");
  const [installing, setInstalling] = useState(false);
  const [disabledKeys, setDisabledKeys] = useState<Set<string>>(new Set());
  const [creationMode, setCreationMode] = useState<"conversation" | "upload" | null>(null);
  const [uploadDraftId, setUploadDraftId] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadPending, setUploadPending] = useState(false);
  const drawerRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [summaries, platformCatalog] = await Promise.all([
        studioClient.listAccessibleDrafts(),
        studioClient.listPlatformSkills(),
      ]);
      const drafts = await Promise.all(
        summaries.filter((summary) => isAgentVisible(summary, showInternalAgents)).map((summary) => studioClient.getDraft(summary.draftId)),
      );
      const studioDrafts = drafts.map(apiDraftToStudioDraft);
      const byName = new Map<string, CatalogSkill>([[
        `platform:${DEFAULT_SKILL_CREATOR.name}`,
        {
          key: `platform:${DEFAULT_SKILL_CREATOR.name}`,
          name: DEFAULT_SKILL_CREATOR.name,
          displayName: "Skill Creator",
          description: DEFAULT_SKILL_CREATOR.description,
          instructions: DEFAULT_SKILL_CREATOR.instructions,
          scope: "platform",
          files: [{ path: "SKILL.md" }],
          fileCount: 1,
          agents: [],
          package: null,
        },
      ]]);
      for (const item of platformCatalog.packages) {
        byName.set(`platform-package:${item.packageId}`, {
          key: `platform-package:${item.packageId}`,
          name: item.skill.name,
          displayName: item.displayName,
          description: item.summary,
          instructions: item.skill.instructions,
          scope: "platform",
          files: item.skill.files ?? [],
          fileCount: item.skill.files?.length ?? 0,
          agents: [],
          package: item,
        });
      }
      for (const draft of studioDrafts) {
        for (const skill of draft.skills) {
          const platformKey = skill.source?.packageId
            ? `platform-package:${skill.source.packageId}`
            : null;
          const platform = platformKey ? byName.get(platformKey) : null;
          if (platform) {
            if (!platform.agents.some((entry) => entry.draftId === draft.id)) {
              platform.agents.push({ draftId: draft.id, label: draft.displayName || draft.name });
            }
            continue;
          }
          const key = `agent:${skill.name}`;
          const existing = byName.get(key);
          const fileCount = skill.fileCount ?? skill.files?.length ?? 0;
          if (existing) {
            if (!existing.agents.some((entry) => entry.draftId === draft.id)) {
              existing.agents.push({ draftId: draft.id, label: draft.displayName || draft.name });
            }
            existing.fileCount = Math.max(existing.fileCount, fileCount);
          } else {
            byName.set(key, {
              key,
              name: skill.name,
              displayName: skill.name,
              description: skill.description || "暂无描述",
              instructions: skill.instructions || "",
              scope: "agent",
              files: (skill.files ?? []).map((file) => file),
              fileCount,
              agents: [{ draftId: draft.id, label: draft.displayName || draft.name }],
              package: null,
            });
          }
        }
      }
      setSkills([...byName.values()].sort((a, b) => a.displayName.localeCompare(b.displayName, "zh-CN")));
      setDrafts(studioDrafts);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "技能目录暂时不可用。");
    } finally {
      setLoading(false);
    }
  }, [showInternalAgents]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(DISABLED_SKILLS_STORAGE_KEY) ?? "[]");
      if (Array.isArray(stored)) {
        setDisabledKeys(new Set(stored.filter((key): key is string => typeof key === "string")));
      }
    } catch {
      setDisabledKeys(new Set());
    }
  }, []);

  const toggleSkill = useCallback((key: string) => {
    setDisabledKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      try {
        window.localStorage.setItem(DISABLED_SKILLS_STORAGE_KEY, JSON.stringify([...next]));
      } catch {
        // The switch still works for this session when local storage is unavailable.
      }
      return next;
    });
  }, []);

  const closeDetail = useCallback(() => setSelectedKey(null), []);
  useDialogFocus({
    open: Boolean(selectedKey),
    panelRef: drawerRef,
    onEscape: closeDetail,
  });

  const visibleSkills = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return skills.filter((skill) =>
      (scopeFilter === "all" || skill.scope === scopeFilter) &&
      (!normalized ||
      [skill.name, skill.displayName, skill.description, ...(skill.package?.tags ?? [])]
        .concat(skill.agents.map((agent) => agent.label))
        .join(" ")
        .toLowerCase()
        .includes(normalized)),
    );
  }, [skills, query, scopeFilter]);

  const selectedSkill = useMemo(
    () => skills.find((skill) => skill.key === selectedKey) ?? null,
    [skills, selectedKey],
  );

  useEffect(() => {
    if (!selectedSkill?.package || drafts.length === 0) {
      setTargetDraftId("");
      return;
    }
    setTargetDraftId((current) =>
      drafts.some((draft) => draft.id === current) ? current : drafts[0]?.id ?? "",
    );
  }, [drafts, selectedSkill]);

  const targetDraft = drafts.find((draft) => draft.id === targetDraftId) ?? null;
  const selectedPackage = selectedSkill?.package ?? null;
  const targetHasCurrentPackage = Boolean(
    selectedPackage
    && targetDraft?.skills.some(
      (skill) => skill.source?.packageId === selectedPackage.packageId
        && skill.source?.contentHash === selectedPackage.contentHash
        && !skill.source.modified,
    ),
  );

  async function installSelectedPackage() {
    if (!selectedSkill?.package || !targetDraft || installing) return;
    setInstalling(true);
    setError("");
    try {
      await studioClient.installPlatformSkill(
        targetDraft.id,
        targetDraft.revision,
        selectedSkill.package.packageId,
        selectedSkill.package.revision,
      );
      await load();
      setNotice(
        `已将 ${selectedSkill.displayName} 导入 ${targetDraft.displayName}；`
        + "后续平台更新不会改变当前草稿快照。",
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "平台 Skill 导入失败。");
    } finally {
      setInstalling(false);
    }
  }

  async function uploadSkill() {
    const target = drafts.find((draft) => draft.id === uploadDraftId);
    if (!target || !uploadFile || uploadPending) return;
    if (!/\.(md|zip)$/i.test(uploadFile.name)) { setError("请选择 SKILL.md 或 ZIP 技能包。"); return; }
    setUploadPending(true);
    setError("");
    try {
      await studioClient.installSkill(target.id, target.revision, uploadFile);
      setNotice(`已上传到 ${target.displayName} 的草稿；发布版本后可在对话中使用。`);
      setUploadFile(null);
      setCreationMode(null);
      await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "上传失败，请重试。"); }
    finally { setUploadPending(false); }
  }

  return (
    <>
      <section className={styles.content}>
        <header className={styles.hero}>
          <h1>技能</h1>
        </header>

        <div className={styles.catalogToolbar}>
          <div className={styles.scopeTabs} aria-label="Skill 作用域">
            {(["all", "personal", "platform", "agent"] as const).map((scope) => (
              <button
                key={scope}
                type="button"
                aria-pressed={scopeFilter === scope}
                onClick={() => setScopeFilter(scope)}
              >
                {scope === "all" ? "全部" : SKILL_SCOPE_LABELS[scope].replace(" Skill", "")}
              </button>
            ))}
          </div>
          <strong>技能 {loading ? "—" : skills.length}</strong>
          <input
            className={styles.searchInput}
            type="search"
            value={query}
            placeholder="搜索技能…"
            onChange={(event) => setQuery(event.target.value)}
            aria-label="搜索技能"
          />
        </div>

        {notice && <p className={styles.notice} role="status">{notice}</p>}
        {error && <p className={styles.error} role="alert">{error}</p>}

        <section className={styles.catalog} aria-label="技能目录">
          <header className={styles.catalogHeader}>
            <div>
              <h2>技能目录</h2>
              <span>{loading ? "…" : skills.length}</span>
            </div>
            <div className={styles.catalogActions}>
              <button type="button" onClick={() => void load()} aria-label="刷新技能目录">↻</button>
              {canManage && <details className={styles.createMenu}>
                <summary className={styles.primary}>＋ 新建</summary>
                <div>
                  <button type="button" onClick={(event) => { setCreationMode("conversation"); event.currentTarget.closest("details")?.removeAttribute("open"); }}><strong>对话创建</strong><span>描述目标，生成技能</span></button>
                  <button type="button" onClick={(event) => { setCreationMode("upload"); setUploadDraftId(drafts[0]?.id ?? ""); event.currentTarget.closest("details")?.removeAttribute("open"); }}><strong>上传技能</strong><span>安装 SKILL.md 或 ZIP 技能包</span></button>
                </div>
              </details>}
            </div>
          </header>

          {creationMode && <section className={styles.creationPanel} aria-label={creationMode === "upload" ? "上传技能" : "对话创建技能"}>
            <header><strong>{creationMode === "upload" ? "上传技能" : "对话创建技能"}</strong><button type="button" onClick={() => setCreationMode(null)} disabled={uploadPending} aria-label="关闭技能创建">×</button></header>
            {creationMode === "conversation" ? <>
              <p>选择技能用途，进入对话描述目标与示例。</p>
              <div className={styles.creationScopes}>
                <Link href={skillCreatorHref("personal")}>个人 Skill</Link>
                <Link href={skillCreatorHref("platform")}>平台 Skill</Link>
                <Link href="/studio/agents?section=skills">Agent Skill · 选择智能体</Link>
              </div>
            </> : <>
              <p>上传已有技能到智能体草稿，发布版本后生效。</p>
              {drafts.length ? <>
                <label>目标智能体<select value={uploadDraftId} disabled={uploadPending} onChange={(event) => setUploadDraftId(event.target.value)}>{drafts.map((draft) => <option key={draft.id} value={draft.id}>{draft.displayName}</option>)}</select></label>
                <label>技能文件<input type="file" accept=".md,.zip" disabled={uploadPending} onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)} /></label>
                <button className={styles.primary} type="button" disabled={!uploadFile || !uploadDraftId || uploadPending} onClick={() => void uploadSkill()}>{uploadPending ? "正在安装…" : "上传并安装"}</button>
              </> : <Link href="/studio/agents">先创建智能体</Link>}
            </>}
          </section>}
          {loading ? (
            <div className={styles.empty}>正在读取技能目录…</div>
          ) : error ? (
            <div className={styles.empty}>
              <button type="button" onClick={() => void load()}>重新加载</button>
            </div>
          ) : skills.length === 0 ? (
            <div className={styles.emptyAction}>
              <strong>尚未安装技能</strong>
              <span>在智能体 Builder 的 Skills 阶段上传 SKILL.md 或 ZIP。</span>
              <Link href="/studio/agents?section=skills">去安装</Link>
            </div>
          ) : visibleSkills.length === 0 ? (
            <div className={styles.empty}>没有匹配的技能</div>
          ) : (
            <div className={styles.groups}>
              <section className={styles.group}>
                {visibleSkills.map((skill) => {
                  const enabled = !disabledKeys.has(skill.key);
                  return (
                    <div className={styles.row} data-enabled={enabled} key={skill.key}>
                      <button
                        className={styles.rowMain}
                        type="button"
                        onClick={() => setSelectedKey(skill.key)}
                      >
                        <span className={styles.glyph} aria-hidden="true">S</span>
                        <span className={styles.rowCopy}>
                          <strong>{skill.displayName}</strong>
                          <small>{skill.description}</small>
                        </span>
                      </button>
                      <div className={styles.rowActions}>
                        <span className={styles.rowMeta}>
                          {SKILL_SCOPE_LABELS[skill.scope]} · {skill.package
                            ? (skill.agents.length ? `${skill.agents.length} 个智能体已导入` : "可导入")
                            : skill.scope === "agent" ? `${skill.agents.length} 个智能体` : "内置"}
                        </span>
                        {skill.package ? (
                          <span className={styles.packageRevision}>r{skill.package.revision}</span>
                        ) : (
                          <button
                            className={styles.skillToggle}
                            type="button"
                            role="switch"
                            aria-checked={enabled}
                            aria-label={`${enabled ? "禁用" : "启用"} ${skill.name}`}
                            title={enabled ? "禁用" : "启用"}
                            onClick={() => toggleSkill(skill.key)}
                          >
                            <span />
                          </button>
                        )}
                        <button
                          className={styles.rowDetail}
                          type="button"
                          aria-label={`查看 ${skill.name} 详情`}
                          onClick={() => setSelectedKey(skill.key)}
                        >
                          ›
                        </button>
                      </div>
                    </div>
                  );
                })}
              </section>
            </div>
          )}
        </section>
      </section>

      {selectedSkill && (
        <div
          className={styles.detailBackdrop}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDetail();
          }}
        >
          <aside
            className={styles.detailDrawer}
            ref={drawerRef}
            role="dialog"
            aria-modal="true"
            aria-label={`${selectedSkill.name} 详情`}
            tabIndex={-1}
          >
          <header className={styles.detailHeader}>
            <span className={styles.glyph} aria-hidden="true">S</span>
            <div>
              <strong>{selectedSkill.displayName}</strong>
              <code>{SKILL_SCOPE_LABELS[selectedSkill.scope]}</code>
            </div>
            <button type="button" onClick={closeDetail} aria-label="关闭详情">×</button>
          </header>
          <div className={styles.detailBody}>
            <p>{selectedSkill.description}</p>
            {selectedSkill.package && (
              <>
                <section className={styles.packageFacts} aria-label="平台 Skill 包信息">
                  <div><span>包修订</span><strong>r{selectedSkill.package.revision}</strong></div>
                  <div><span>风险</span><strong>{selectedSkill.package.riskLevel === "low" ? "低" : "需审阅"}</strong></div>
                  <div><span>许可证</span><strong>{selectedSkill.package.license}</strong></div>
                  <div><span>内容哈希</span><code>{selectedSkill.package.contentHash.slice(0, 12)}</code></div>
                </section>
                <section className={styles.detailSection}>
                  <h4>来源与兼容性</h4>
                  <a href={selectedSkill.package.sourceUrl} target="_blank" rel="noreferrer">
                    {selectedSkill.package.sourceRevision}
                  </a>
                  <span>{selectedSkill.package.compatibleRuntimes.join(" · ")}</span>
                </section>
              </>
            )}
            {selectedSkill.agents.length > 0 && (
              <section className={styles.detailSection}>
                <h4>来源智能体</h4>
                <ul className={styles.agentList}>
                  {selectedSkill.agents.map((agent) => (
                    <li key={agent.draftId}>
                      <Link href={`/studio/agents?draft=${encodeURIComponent(agent.draftId)}&section=skills`}>
                        {agent.label}
                      </Link>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {selectedSkill.instructions && (
              <section className={styles.detailSection}>
                <h4>Instructions</h4>
                <pre>{selectedSkill.instructions}</pre>
              </section>
            )}
            <section className={styles.detailSection}>
              <h4>附加文件（{selectedSkill.fileCount}）</h4>
              <ul className={styles.fileList}>
                {selectedSkill.files.map((file) => (
                  <li key={file.path}>
                    <code>{file.path}</code>
                  </li>
                ))}
              </ul>
            </section>
            <section className={styles.detailSection}>
              <h4>最近状态</h4>
              <span>{selectedSkill.package
                ? "平台包只在导入时复制；平台后续更新不会改变已导入或已发布版本。"
                : `${disabledKeys.has(selectedSkill.key) ? "已禁用" : "已启用"} · 需要在新版本发布时固化为不可变快照。`}</span>
            </section>
          </div>
          {canManage && (
            <footer className={styles.drawerActions}>
              {selectedSkill.package ? (
                canManageCatalog ? (
                <div className={styles.installControls}>
                  {drafts.length > 0 ? (
                    <>
                      <label htmlFor="platform-skill-target">导入到</label>
                      <select
                        id="platform-skill-target"
                        value={targetDraftId}
                        onChange={(event) => setTargetDraftId(event.target.value)}
                      >
                        {drafts.map((draft) => (
                          <option key={draft.id} value={draft.id}>{draft.displayName}</option>
                        ))}
                      </select>
                      <button
                        className={styles.actionButton}
                        type="button"
                        disabled={installing || targetHasCurrentPackage}
                        onClick={() => void installSelectedPackage()}
                      >
                        {installing
                          ? "正在导入…"
                          : targetHasCurrentPackage ? "已导入当前快照" : "导入草稿快照"}
                      </button>
                    </>
                  ) : (
                    <Link className={styles.actionLink} href="/studio/agents">先创建智能体</Link>
                  )}
                </div>
                ) : (
                  <span className={styles.actionLink}>平台 Skill 由管理员统一导入，如需使用请联系管理员。</span>
                )
              ) : (
                <button
                  className={styles.secondaryAction}
                  type="button"
                  onClick={() => toggleSkill(selectedSkill.key)}
                >
                  {disabledKeys.has(selectedSkill.key) ? "启用" : "禁用"}
                </button>
              )}
              {!selectedSkill.package && !disabledKeys.has(selectedSkill.key) && (
                <Link
                  className={styles.actionLink}
                  href={selectedSkill.name === DEFAULT_SKILL_CREATOR.name
                    ? skillCreatorHref("personal")
                    : `/studio/agents?draft=${encodeURIComponent(selectedSkill.agents[0]?.draftId ?? "")}&section=skills`}
                >
                  {selectedSkill.name === DEFAULT_SKILL_CREATOR.name ? "用它创建 Skill" : "前往智能体管理"}
                </Link>
              )}
            </footer>
          )}
          </aside>
        </div>
      )}
    </>
  );
}
