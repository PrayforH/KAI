"use client";
import { FeedbackToast } from "../feedback-toast";

import { useInternalAgentsPreference } from "../../lib/interface-preferences";
import { isAgentVisible } from "../../lib/agent-visibility";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth-provider";
import {
  studioClient,
  type StudioAgentSkillCatalogEntry,
  type StudioPlatformSkillPackage,
} from "../../lib/studio-client";
import { useDialogFocus } from "../../lib/use-dialog-focus";
import {
  deletePersonalSkill,
  dismissSkillProposal,
  generateSkillProposal,
  listPersonalSkills,
  listSkillProposals,
  saveSkillProposal,
  setPersonalSkillEnabled,
  type PersonalSkill,
  type SkillProposal,
} from "../../lib/assistant-client";
import {
  DEFAULT_SKILL_CREATOR,
  SKILL_SCOPE_LABELS,
  skillCreatorHref,
  type SkillInstallScope,
} from "../../lib/skill-creator-launch";
import styles from "./skills-catalog-page.module.css";
import { matchesSkillQuery } from "../../lib/skill-catalog-search";

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
  personalSkill: PersonalSkill | null;
}

const HIDDEN_SKILLS_STORAGE_KEY = "harness-skill-catalog-hidden:v1";

const CATALOG_PAGE_SIZE = 20;

const SCOPE_HINTS: Record<"all" | SkillInstallScope, string> = {
  all: "个人 Skill 只对你生效；全局 Skill 由工作区统一维护；Agent Skill 随智能体版本发布。",
  personal: "只作用于你的个人助理：保存后默认停用，启用后才会注入新任务。",
  platform: "工作区统一维护，可导入任意智能体的草稿快照。",
  agent: "随智能体草稿发布，只对该智能体生效。",
};

/** Merge the platform package catalog with the Skills declared by agent
 *  drafts into the single row list the page renders. */
export function buildCatalog(
  platformPackages: StudioPlatformSkillPackage[],
  studioDrafts: StudioAgentSkillCatalogEntry[],
  personalSkills: PersonalSkill[] = [],
): CatalogSkill[] {
  const byName = new Map<string, CatalogSkill>();
  for (const skill of personalSkills) {
    byName.set(`personal-skill:${skill.skillId}`, {
      key: `personal-skill:${skill.skillId}`,
      name: skill.name,
      displayName: skill.displayName || skill.name,
      description: skill.description,
      instructions: skill.skill.instructions,
      scope: "personal",
      files: (skill.skill.files ?? []).map((file) => ({ path: file.path })),
      fileCount: skill.skill.files?.length ?? 0,
      agents: [],
      package: null,
      personalSkill: skill,
    });
  }
  for (const item of platformPackages) {
    byName.set(`platform-package:${item.packageId}`, {
      key: `platform-package:${item.packageId}`,
      name: item.skill.name,
      displayName: item.displayName,
      description: item.summary,
      instructions: item.skill.instructions,
      scope: "platform",
      files: item.skill.files,
      // A web bundle can briefly outrun the API during a rolling deploy, so
      // fall back to the listed files when the count is absent.
      fileCount: item.skill.fileCount ?? item.skill.files.length,
      agents: [],
      package: item,
      personalSkill: null,
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
          personalSkill: null,
        });
      }
    }
  }
  return [...byName.values()].sort((a, b) => a.displayName.localeCompare(b.displayName, "zh-CN"));
}

export function SkillsCatalogPage() {
  const { membership } = useAuth();
  const [showInternalAgents] = useInternalAgentsPreference();
  const canManage = membership.role !== "viewer";
  // Mounting reviewed platform Skills requires the backend catalog-admin
  // permission (owner/admin); keep the UI gate aligned to avoid 403s.
  const canManageCatalog = membership.role === "owner" || membership.role === "admin";
  const [skills, setSkills] = useState<CatalogSkill[]>([]);
  const [drafts, setDrafts] = useState<StudioAgentSkillCatalogEntry[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scopeFilter, setScopeFilter] = useState<"all" | SkillInstallScope>("all");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [targetDraftId, setTargetDraftId] = useState("");
  const [installing, setInstalling] = useState(false);
  const [hiddenKeys, setHiddenKeys] = useState<Set<string>>(new Set());
  const [creationMode, setCreationMode] = useState<"conversation" | "upload" | null>(null);
  const [uploadDraftId, setUploadDraftId] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadPending, setUploadPending] = useState(false);
  const [personalPending, setPersonalPending] = useState<string | null>(null);
  const [proposals, setProposals] = useState<SkillProposal[]>([]);
  const [proposalPending, setProposalPending] = useState<string | null>(null);
  const [renderLimit, setRenderLimit] = useState(CATALOG_PAGE_SIZE);
  const loadGeneration = useRef(0);
  const drawerRef = useRef<HTMLElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const generation = ++loadGeneration.current;
    try {
      const [agents, platformCatalog, personal, nextProposals] = await Promise.all([
        studioClient.listAgentSkills(),
        studioClient.listPlatformSkills(),
        // Older deployments may not expose the personal Skill API yet; the
        // rest of the catalog must still render in that case.
        listPersonalSkills().catch(() => []),
        listSkillProposals().catch(() => []),
      ]);
      if (generation !== loadGeneration.current) return;
      const visible = agents.filter((agent) => isAgentVisible(agent, showInternalAgents));
      setSkills(buildCatalog(platformCatalog.packages, visible, personal));
      setDrafts(visible);
      setProposals(nextProposals);
    } catch (caught) {
      if (generation === loadGeneration.current) setError(caught instanceof Error ? caught.message : "技能目录暂时不可用。");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, [showInternalAgents]);

  useEffect(() => {
    void load();
    return () => { loadGeneration.current += 1; };
  }, [load]);

  useEffect(() => {
    try {
      const stored = JSON.parse(window.localStorage.getItem(HIDDEN_SKILLS_STORAGE_KEY) ?? "[]");
      if (Array.isArray(stored)) {
        setHiddenKeys(new Set(stored.filter((key): key is string => typeof key === "string")));
      }
    } catch {
      setHiddenKeys(new Set());
    }
  }, []);

  const toggleSkill = useCallback((key: string) => {
    setHiddenKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      try {
        window.localStorage.setItem(HIDDEN_SKILLS_STORAGE_KEY, JSON.stringify([...next]));
      } catch {
        // The switch still works for this session when local storage is unavailable.
      }
      return next;
    });
  }, []);

  async function togglePersonal(skill: PersonalSkill) {
    if (personalPending) return;
    setPersonalPending(skill.skillId);
    setError("");
    try {
      const updated = await setPersonalSkillEnabled(
        skill.skillId,
        !skill.enabled,
        skill.revision,
      );
      await load();
      setNotice(
        updated.enabled
          ? `已启用 ${updated.displayName}；之后的个人助理任务会注入它。`
          : `已停用 ${updated.displayName}；之后的个人助理任务不再注入它。`,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "修改个人 Skill 状态失败。");
    } finally {
      setPersonalPending(null);
    }
  }

  async function removePersonal(skill: PersonalSkill) {
    if (personalPending) return;
    if (!window.confirm(`删除个人 Skill“${skill.displayName}”？之后的个人助理任务不再使用它。`)) return;
    setPersonalPending(skill.skillId);
    setError("");
    try {
      const result = await deletePersonalSkill(skill.skillId);
      if (selectedKey === `personal-skill:${skill.skillId}`) setSelectedKey(null);
      await load();
      setNotice(result.deleted ? `已删除 ${skill.displayName}。` : `${skill.displayName} 已不存在。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除个人 Skill 失败。");
    } finally {
      setPersonalPending(null);
    }
  }

  async function actOnProposal(
    proposal: SkillProposal,
    action: "generate" | "save" | "dismiss",
  ) {
    if (proposalPending) return;
    setProposalPending(`${proposal.proposalId}:${action}`);
    setError("");
    try {
      if (action === "generate") {
        await generateSkillProposal(proposal.proposalId);
        setNotice("已生成草案，确认内容后保存为个人 Skill。");
      } else if (action === "save") {
        const saved = await saveSkillProposal(proposal.proposalId, proposal.revision);
        setNotice(
          `已保存「${saved.displayName}」，默认停用；在个人 Skill 中启用后才会注入新任务。`,
        );
        await load();
        setSelectedKey(`personal-skill:${saved.skillId}`);
        return;
      } else {
        await dismissSkillProposal(proposal.proposalId, proposal.revision);
        setNotice("已忽略这条建议，7 天内不再重复提示。");
      }
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "个人 Skill 建议操作失败。");
    } finally {
      setProposalPending(null);
    }
  }

  const closeDetail = useCallback(() => setSelectedKey(null), []);
  useDialogFocus({
    open: Boolean(selectedKey),
    panelRef: drawerRef,
    onEscape: closeDetail,
  });

  const visibleSkills = useMemo(() => {
    return skills.filter((skill) =>
      (scopeFilter === "all" || skill.scope === scopeFilter) &&
      matchesSkillQuery(skill, query),
    );
  }, [skills, query, scopeFilter]);

  // Proposals belong to the personal scope; they stay visible under 全部 so a
  // new suggestion is not missed just because another filter was left selected.
  const activeProposals = useMemo(
    () => proposals.filter(
      (proposal) => proposal.status === "proposed" || proposal.status === "draft",
    ),
    [proposals],
  );
  const showProposals = activeProposals.length > 0
    && (scopeFilter === "all" || scopeFilter === "personal");

  // The catalog can hold hundreds of rows; render the list in scroll-sized
  // pages so opening the page never builds every row at once.
  const pageSkills = useMemo(
    () => visibleSkills.slice(0, renderLimit),
    [visibleSkills, renderLimit],
  );
  const hasMoreRows = pageSkills.length < visibleSkills.length;

  useEffect(() => {
    setRenderLimit(CATALOG_PAGE_SIZE);
  }, [query, scopeFilter, skills.length]);

  useEffect(() => {
    if (!hasMoreRows) return;
    const sentinel = sentinelRef.current;
    if (!sentinel || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setRenderLimit((current) => current + CATALOG_PAGE_SIZE);
        }
      },
      { rootMargin: "320px 0px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [hasMoreRows, pageSkills.length]);

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
  const selectedPersonal = selectedSkill?.personalSkill ?? null;
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
      setError(caught instanceof Error ? caught.message : "全局 Skill 导入失败。");
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
        <p className={styles.scopeHint}>{SCOPE_HINTS[scopeFilter]}</p>

        <FeedbackToast message={notice} onDismiss={() => setNotice("")} />
        <FeedbackToast message={error} tone="error" onDismiss={() => setError("")} />

        {showProposals && (
          <section className={styles.proposals} aria-label="个人 Skill 建议">
            <header>
              <div>
                <h2>个人 Skill 建议</h2>
                <span>{activeProposals.length}</span>
              </div>
              <small>来自你重复完成的任务；保存后默认停用。</small>
            </header>
            {activeProposals.map((proposal) => {
              const draft = proposal.draft;
              return (
                <article key={proposal.proposalId} className={styles.proposal}>
                  <div className={styles.proposalCopy}>
                    <strong>{proposal.title}</strong>
                    <p>{proposal.summary}</p>
                    <small>{proposal.sources.length} 个任务 · 来自你的执行记录</small>
                    {proposal.reply && (
                      <p className={styles.proposalReply}>{proposal.reply}</p>
                    )}
                    {proposal.followUpQuestions?.map((question) => (
                      <small key={question}>· {question}</small>
                    ))}
                    {draft && (
                      <details className={styles.proposalDraft}>
                        <summary>{draft.name}</summary>
                        <p>{draft.description}</p>
                        <pre>{draft.instructions}</pre>
                      </details>
                    )}
                  </div>
                  <div className={styles.proposalActions}>
                    {!draft && (
                      <button
                        type="button"
                        disabled={proposalPending !== null}
                        onClick={() => void actOnProposal(proposal, "generate")}
                      >
                        {proposalPending === `${proposal.proposalId}:generate`
                          ? "生成中…"
                          : proposal.reply ? "继续生成" : "生成草稿"}
                      </button>
                    )}
                    {draft && (
                      <button
                        type="button"
                        disabled={proposalPending !== null}
                        onClick={() => void actOnProposal(proposal, "save")}
                      >
                        {proposalPending === `${proposal.proposalId}:save`
                          ? "保存中…"
                          : "保存为个人 Skill"}
                      </button>
                    )}
                    <button
                      className={styles.secondaryAction}
                      type="button"
                      disabled={proposalPending !== null}
                      onClick={() => void actOnProposal(proposal, "dismiss")}
                    >
                      {proposalPending === `${proposal.proposalId}:dismiss` ? "忽略中…" : "忽略"}
                    </button>
                  </div>
                </article>
              );
            })}
          </section>
        )}

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
                <Link href={skillCreatorHref("platform")}>全局 Skill</Link>
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
            <div className={styles.empty}>
              {scopeFilter === "personal"
                ? "暂无个人 Skill。重复完成同类任务后，这里会出现可复用的建议。"
                : "没有匹配的技能"}
            </div>
          ) : (
            <div className={styles.groups}>
              <section className={styles.group}>
                {pageSkills.map((skill) => {
                  const visible = Boolean(skill.personalSkill) || !hiddenKeys.has(skill.key);
                  return (
                    <div className={styles.row} data-enabled={visible} key={skill.key}>
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
                          {skill.personalSkill
                            ? `${SKILL_SCOPE_LABELS.personal} · ${skill.personalSkill.enabled ? "已启用" : "未启用"}`
                            : <>{SKILL_SCOPE_LABELS[skill.scope]} · {skill.package
                              ? (skill.agents.length ? `${skill.agents.length} 个智能体已导入` : "可导入")
                              : skill.scope === "agent" ? `${skill.agents.length} 个智能体` : "内置"}</>}
                        </span>
                        {skill.package ? (
                          <span className={styles.packageRevision}>r{skill.package.revision}</span>
                        ) : skill.personalSkill ? (
                          <span className={styles.packageRevision}>r{skill.personalSkill.revision}</span>
                        ) : (
                          <button
                            className={styles.skillToggle}
                            type="button"
                            aria-pressed={!visible}
                            aria-label={`${visible ? "从本机列表隐藏" : "在本机列表显示"} ${skill.name}（不改变平台启用状态）`}
                            title={visible ? "从本机列表隐藏（不改变平台启用状态）" : "在本机列表显示"}
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
              {hasMoreRows && (
                <div className={styles.loadMore} ref={sentinelRef}>
                  <span>{`已显示 ${pageSkills.length} / ${visibleSkills.length}`}</span>
                  <button
                    type="button"
                    onClick={() => setRenderLimit((current) => current + CATALOG_PAGE_SIZE)}
                  >
                    加载更多
                  </button>
                </div>
              )}
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
                <section className={styles.packageFacts} aria-label="全局 Skill 包信息">
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
              <span>{selectedSkill.personalSkill
                ? `${selectedSkill.personalSkill.enabled
                  ? "已启用：之后的个人助理任务会注入此 Skill"
                  : "未启用：保存后不会注入任何任务"} · r${selectedSkill.personalSkill.revision}`
                : selectedSkill.package
                  ? "平台包只在导入时复制；平台后续更新不会改变已导入或已发布版本。"
                  : `${hiddenKeys.has(selectedSkill.key) ? "已从本机列表隐藏" : "在本机列表中显示"}（不影响平台启用状态） · 需要在新版本发布时固化为不可变快照。`}</span>
            </section>
          </div>
          {canManage && (
            <footer className={styles.drawerActions}>
              {selectedPersonal ? (
                <>
                  <button
                    className={styles.actionButton}
                    type="button"
                    disabled={personalPending === selectedPersonal.skillId}
                    onClick={() => void togglePersonal(selectedPersonal)}
                  >
                    {personalPending === selectedPersonal.skillId
                      ? "正在保存…"
                      : selectedPersonal.enabled ? "停用" : "启用"}
                  </button>
                  <button
                    className={styles.dangerAction}
                    type="button"
                    disabled={personalPending === selectedPersonal.skillId}
                    onClick={() => void removePersonal(selectedPersonal)}
                  >
                    删除
                  </button>
                </>
              ) : selectedSkill.package ? (
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
                  <span className={styles.actionLink}>全局 Skill 由管理员统一导入，如需使用请联系管理员。</span>
                )
              ) : (
                <button
                  className={styles.secondaryAction}
                  type="button"
                  onClick={() => toggleSkill(selectedSkill.key)}
                >
                  {hiddenKeys.has(selectedSkill.key) ? "在本机列表显示" : "从本机列表隐藏"}
                </button>
              )}
              {!selectedPersonal
                && (selectedSkill.name === DEFAULT_SKILL_CREATOR.name || !selectedSkill.package)
                && !hiddenKeys.has(selectedSkill.key) && (
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
