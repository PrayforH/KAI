"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth-provider";
import {
  studioClient,
  type KnowledgeBaseType,
  type StudioKnowledgeBase,
  type StudioKnowledgeBaseConfig,
  type WikiGranularity,
  StudioApiError,
} from "../../lib/studio-client";
import { isValidKnowledgeReference, slugifyKnowledgeReference } from "../../lib/knowledge-reference";
import { KnowledgeMembersPanel } from "./knowledge-members-panel";
import { KnowledgeDrawerLayer } from "./knowledge-drawer-layer";
import styles from "./knowledge-console.module.css";
import { StudioPageHeader } from "../agent-studio/studio-page-header";

const KB_TYPE_LABELS: Record<KnowledgeBaseType, string> = {
  rag: "RAG",
  wiki: "Wiki",
  hybrid: "混合",
};

const KB_TYPE_HINTS: Record<KnowledgeBaseType, string> = {
  rag: "文档切片 + 向量/关键词混合检索，引用可回溯到切片",
  wiki: "自动生成互链的 Wiki 页面（摘要/实体/概念）与图谱",
  hybrid: "RAG 切片检索 + Wiki 页面检索聚合，能力最全",
};

const GRANULARITY_ORDER: readonly WikiGranularity[] = ["focused", "standard", "exhaustive"];

const GRANULARITY_LABELS: Record<WikiGranularity, string> = {
  focused: "聚焦",
  standard: "标准",
  exhaustive: "详尽",
};

const GRANULARITY_HINTS: Record<WikiGranularity, string> = {
  focused: "只抽取文档的主角（如简历 → 人物和项目）。最干净，但可能漏掉次要实体。",
  standard: "抽取主角 + 被详细描述的次要实体/概念。跳过一带而过的通用名词。适合大多数场景。",
  exhaustive: "抽取所有可识别的命名实体与概念，包括一带而过的技术栈。适合将知识库当作术语表使用。",
};

/** A blank tuning field means "keep the engine default", never zero. */
function optionalNumber(value: string): number | undefined {
  const parsed = Number(value);
  return value.trim() !== "" && Number.isFinite(parsed) ? parsed : undefined;
}

type TypeFilter = "all" | "mine";

export function KnowledgeConsole() {
  const { membership, user } = useAuth();
  const canManage = membership.role !== "viewer";
  const [bases, setBases] = useState<StudioKnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState<TypeFilter>("all");
  const [showCreate, setShowCreate] = useState(false);
  const [kbType, setKbType] = useState<KnowledgeBaseType>("rag");
  const [reference, setReference] = useState("");
  // The identifier is suggested from the name until the operator types one.
  const referenceEdited = useRef(false);
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [chunkSize, setChunkSize] = useState("");
  const [chunkOverlap, setChunkOverlap] = useState("");
  const [granularity, setGranularity] = useState<WikiGranularity>("standard");
  const [wikiContentInstructions, setWikiContentInstructions] = useState("");
  const [wikiExtractionInstructions, setWikiExtractionInstructions] = useState("");
  const [wikiMaxPages, setWikiMaxPages] = useState("");
  const [membersFor, setMembersFor] = useState<StudioKnowledgeBase | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setBases(await studioClient.listKnowledgeBases());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载知识库失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // The card "⋯" menu closes on any click outside of it, or on Escape.
  useEffect(() => {
    if (menuFor === null) return;
    const close = (event: Event) => {
      const target = event.target;
      if (target instanceof Element && target.closest("[data-kb-menu]")) return;
      setMenuFor(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuFor(null);
    };
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuFor]);

  const onDeleteBase = useCallback(
    async (base: StudioKnowledgeBase) => {
      if (
        !window.confirm(
          `删除知识库「${base.displayName}」？WeKnora 中该库及其全部文档、Wiki 与图谱将一并删除，不可恢复。`,
        )
      ) {
        return;
      }
      setMenuFor(null);
      setDeleting(base.reference);
      setError("");
      setNotice("");
      try {
        await studioClient.deleteKnowledgeBase(base.reference);
        setNotice(`知识库「${base.displayName}」已删除`);
        await load();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "删除知识库失败");
      } finally {
        setDeleting(null);
      }
    },
    [load],
  );

  const visible = useMemo(() => {
    if (filter === "mine" && user?.user_id) {
      return bases.filter((base) => base.createdBy === user.user_id);
    }
    return bases;
  }, [bases, filter, user?.user_id]);

  const referenceValid = isValidKnowledgeReference(reference);
  // The list is already loaded here, so the form can rule out an identifier that
  // would come back as a 409 conflict instead of submitting it.
  const referenceTaken = bases.some((base) => base.reference === reference.trim());
  const createDisabled = creating || !referenceValid || referenceTaken || !displayName.trim();

  const usesRag = kbType === "rag" || kbType === "hybrid";
  const usesWiki = kbType === "wiki" || kbType === "hybrid";

  const resetDraft = useCallback(() => {
    setReference("");
    setDisplayName("");
    setDescription("");
    setChunkSize("");
    setChunkOverlap("");
    setGranularity("standard");
    setWikiContentInstructions("");
    setWikiExtractionInstructions("");
    setWikiMaxPages("");
  }, []);

  // Only the groups the chosen type actually uses are sent, so a RAG base never
  // carries Wiki options and vice versa.
  const draftConfig = useCallback((): StudioKnowledgeBaseConfig => {
    const config: StudioKnowledgeBaseConfig = {};
    if (usesRag) {
      const size = optionalNumber(chunkSize);
      const overlap = optionalNumber(chunkOverlap);
      if (size !== undefined) config.chunkSize = size;
      if (overlap !== undefined) config.chunkOverlap = overlap;
    }
    if (usesWiki) {
      config.wikiGranularity = granularity;
      if (wikiContentInstructions.trim()) {
        config.wikiContentInstructions = wikiContentInstructions.trim();
      }
      if (wikiExtractionInstructions.trim()) {
        config.wikiExtractionInstructions = wikiExtractionInstructions.trim();
      }
      const pages = optionalNumber(wikiMaxPages);
      if (pages !== undefined) config.wikiMaxPagesPerIngest = pages;
    }
    return config;
  }, [
    chunkOverlap,
    chunkSize,
    granularity,
    usesRag,
    usesWiki,
    wikiContentInstructions,
    wikiExtractionInstructions,
    wikiMaxPages,
  ]);

  const submit = useCallback(async () => {
    if (createDisabled) return;
    setCreating(true);
    setError("");
    setNotice("");
    try {
      const created = await studioClient.createKnowledgeBase({
        reference: reference.trim(),
        displayName: displayName.trim(),
        description: description.trim(),
        sourceReferences: [],
        kbType,
        engine: "weknora",
        config: draftConfig(),
      });
      setShowCreate(false);
      setNotice(`知识库「${created.displayName}」已创建`);
      resetDraft();
      try {
        await load();
      } catch {
        // The base exists; only the list refresh failed. Never report it as a
        // failed creation or the operator retries and hits a 409.
        setError(`知识库「${created.displayName}」已创建，但列表刷新失败，请手动刷新页面。`);
      }
    } catch (cause) {
      if (cause instanceof StudioApiError && cause.status === 409) {
        setError(`标识「${reference.trim()}」已被占用。它可能就是你之前创建成功的那一个：请先关闭本窗口，在列表里确认；或换一个标识再建。`);
      } else if (cause instanceof StudioApiError && cause.status === 422) {
        setError("创建被拒绝：标识需为小写字母、数字与连字符，并以字母开头。");
      } else {
        setError(cause instanceof Error ? cause.message : "创建知识库失败");
      }
    } finally {
      setCreating(false);
    }
  }, [createDisabled, description, displayName, draftConfig, kbType, load, reference, resetDraft]);

  return (
    <section className={styles.content}>
        <StudioPageHeader
          ariaLabel="知识库范围"
          active={filter}
          onSelect={setFilter}
          tabs={[
            { id: "all", label: `全部 ${bases.length}` },
            {
              id: "mine",
              label: `我创建的 ${
                user?.user_id
                  ? bases.filter((base) => base.createdBy === user.user_id).length
                  : 0
              }`,
            },
          ]}
        >
          {canManage ? (
            <button
              type="button"
              className={styles.primary}
              onClick={() => setShowCreate(true)}
            >
              新建知识库
            </button>
          ) : null}
        </StudioPageHeader>

        {error ? <p className={styles.error}>{error}</p> : null}
        {notice ? <p className={styles.notice}>{notice}</p> : null}

        {loading ? (
          <p className={styles.empty}>加载中…</p>
        ) : visible.length === 0 ? (
          <p className={styles.empty}>
            还没有知识库。点击「新建知识库」创建第一个 RAG / Wiki / 混合知识库。
          </p>
        ) : (
          <div className={styles.grid}>
            {visible.map((base) => (
              <article key={base.reference} className={styles.card}>
                <Link
                  href={`/studio/knowledge/${encodeURIComponent(base.reference)}`}
                  className={styles.cardBody}
                >
                  <div className={styles.cardHead}>
                    <span className={styles.cardGlyph} aria-hidden="true">
                      <svg viewBox="0 0 20 20">
                        <path d="M10 5.2C8.4 4.2 6.3 3.8 3.8 4v11c2.5-.2 4.6.2 6.2 1.2 1.6-1 3.7-1.4 6.2-1.2V4c-2.5-.2-4.6.2-6.2 1.2z" />
                        <path d="M10 5.2v11" />
                      </svg>
                    </span>
                    <h3 className={styles.cardTitle} title={base.displayName}>{base.displayName}</h3>
                  </div>
                  <p className={styles.cardDesc}>
                    {base.description || "暂无描述"}
                  </p>
                </Link>
                <button
                  type="button"
                  className={styles.cardMenuTrigger}
                  data-kb-menu
                  aria-label={`${base.displayName} 更多操作`}
                  aria-expanded={menuFor === base.reference}
                  onClick={() =>
                    setMenuFor((current) => (current === base.reference ? null : base.reference))
                  }
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true">
                    <circle cx="4" cy="10" r="1.4" />
                    <circle cx="10" cy="10" r="1.4" />
                    <circle cx="16" cy="10" r="1.4" />
                  </svg>
                </button>
                {menuFor === base.reference ? (
                  <div className={styles.cardMenu} data-kb-menu role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      className={styles.cardMenuItem}
                      onClick={() => {
                        setMenuFor(null);
                        setMembersFor(base);
                      }}
                    >
                      成员管理
                    </button>
                    {canManage ? (
                      <button
                        type="button"
                        role="menuitem"
                        className={`${styles.cardMenuItem} ${styles.cardMenuItemDanger}`}
                        disabled={deleting === base.reference}
                        onClick={() => void onDeleteBase(base)}
                      >
                        {deleting === base.reference ? "删除中…" : "删除"}
                      </button>
                    ) : null}
                  </div>
                ) : null}
                <div className={styles.cardMeta}>
                  <span className={styles.countBadge} title="文档数量">
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <path d="M3.5 5.5h5l1.5 2h6.5v7h-13z" />
                    </svg>
                    {base.documentCount}
                  </span>
                  <span className={styles.cardRef}>{base.reference}</span>
                </div>
              </article>
            ))}
          </div>
        )}

        {membersFor ? (
          <KnowledgeDrawerLayer onClose={() => setMembersFor(null)}>
          <div
            className={styles.overlay}
            role="presentation"
            onClick={(event) => {
              if (event.target === event.currentTarget) setMembersFor(null);
            }}
          >
            <div
              className={styles.membersDialog}
              role="dialog"
              aria-modal="true"
              aria-label={`${membersFor.displayName} 成员管理`}
            >
              <header className={styles.membersDialogHead}>
                <h2>{membersFor.displayName} · 成员管理</h2>
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => setMembersFor(null)}
                >
                  关闭
                </button>
              </header>
              <KnowledgeMembersPanel reference={membersFor.reference} />
            </div>
          </div>
          </KnowledgeDrawerLayer>
        ) : null}

        {showCreate ? (
          <KnowledgeDrawerLayer onClose={() => setShowCreate(false)}>
          <div
            className={styles.overlay}
            role="presentation"
            onClick={(event) => {
              if (event.target === event.currentTarget) setShowCreate(false);
            }}
          >
            <div
              className={styles.dialog}
              role="dialog"
              aria-modal="true"
              aria-label="新建知识库"
            >
              <h2>新建知识库</h2>
              <p className={styles.dialogHint}>
                选择适合资料的知识库类型，上传文档后即可检索与问答。
              </p>
              <div className={styles.typeRow}>
                {(Object.keys(KB_TYPE_LABELS) as KnowledgeBaseType[]).map((type) => (
                  <button
                    key={type}
                    type="button"
                    className={`${styles.typeCard} ${kbType === type ? styles.typeCardActive : ""}`}
                    aria-pressed={kbType === type}
                    onClick={() => setKbType(type)}
                  >
                    <p className={styles.typeName}>{KB_TYPE_LABELS[type]}</p>
                    <p className={styles.typeDesc}>{KB_TYPE_HINTS[type]}</p>
                  </button>
                ))}
              </div>
              <div className={styles.field}>
                <label htmlFor="kb-reference">标识（小写字母与连字符）</label>
                <input
                  id="kb-reference"
                  value={reference}
                  aria-invalid={reference.trim().length > 0 && !referenceValid}
                  aria-describedby="kb-reference-hint"
                  onChange={(event) => { setReference(event.target.value); referenceEdited.current = true; }}
                  placeholder="case-library"
                />
                <p
                  id="kb-reference-hint"
                  className={referenceTaken || (reference.trim() && !referenceValid) ? styles.fieldError : styles.fieldHint}
                >
                  {referenceTaken
                    ? `标识「${reference.trim()}」已经存在，请换一个（例如 ${reference.trim()}-2）。`
                    : reference.trim() && !referenceValid
                      ? "标识只能使用小写字母、数字和连字符，并以字母开头，例如 case-library。"
                      : "标识用于地址与检索，创建后不可修改。"}
                </p>
              </div>
              <div className={styles.field}>
                <label htmlFor="kb-name">名称</label>
                <input
                  id="kb-name"
                  value={displayName}
                  onChange={(event) => {
                    setDisplayName(event.target.value);
                    if (!referenceEdited.current) setReference(slugifyKnowledgeReference(event.target.value));
                  }}
                  placeholder="非法集资案例库"
                />
              </div>
              <div className={styles.field}>
                <label htmlFor="kb-desc">描述（可选）</label>
                <textarea
                  id="kb-desc"
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="收录 2017-2020 年非法集资典型案例与法规切片"
                />
              </div>

              {usesRag ? (
                <fieldset className={styles.configGroup}>
                  <legend className={styles.configLegend}>分块设置</legend>
                  <p className={styles.configHint}>
                    RAG 检索按此还原原文切片；留空使用平台默认值，已有内容不受影响。
                  </p>
                  <div className={styles.configRow}>
                    <div className={styles.field}>
                      <label htmlFor="kb-chunk-size">分块大小（字符）</label>
                      <input
                        id="kb-chunk-size"
                        type="number"
                        min={200}
                        max={20000}
                        value={chunkSize}
                        onChange={(event) => setChunkSize(event.target.value)}
                        placeholder="4000"
                      />
                    </div>
                    <div className={styles.field}>
                      <label htmlFor="kb-chunk-overlap">分块重叠（字符）</label>
                      <input
                        id="kb-chunk-overlap"
                        type="number"
                        min={0}
                        max={4000}
                        value={chunkOverlap}
                        onChange={(event) => setChunkOverlap(event.target.value)}
                        placeholder="100"
                      />
                    </div>
                  </div>
                </fieldset>
              ) : null}

              {usesWiki ? (
                <fieldset className={styles.configGroup}>
                  <legend className={styles.configLegend}>Wiki 设置</legend>
                  <div className={styles.field}>
                    <span className={styles.fieldLabel} id="kb-granularity-label">
                      提取粒度
                    </span>
                    <div
                      className={styles.segmented}
                      role="group"
                      aria-labelledby="kb-granularity-label"
                    >
                      {GRANULARITY_ORDER.map((value) => (
                        <button
                          key={value}
                          type="button"
                          className={`${styles.segmentedButton} ${
                            granularity === value ? styles.segmentedActive : ""
                          }`}
                          aria-pressed={granularity === value}
                          onClick={() => setGranularity(value)}
                        >
                          {GRANULARITY_LABELS[value]}
                        </button>
                      ))}
                    </div>
                    <p className={styles.configHint}>{GRANULARITY_HINTS[granularity]}</p>
                  </div>
                  <div className={styles.field}>
                    <label htmlFor="kb-wiki-content">Wiki 内容生成要求（可选）</label>
                    <textarea
                      id="kb-wiki-content"
                      maxLength={4000}
                      value={wikiContentInstructions}
                      onChange={(event) => setWikiContentInstructions(event.target.value)}
                      placeholder="例如：使用法务审阅口吻，优先展示责任主体、时间线和风险提示…"
                    />
                    <p className={styles.configCount}>{wikiContentInstructions.length}/4000</p>
                    <p className={styles.configHint}>
                      控制摘要、页面与首页的表达重点；引用、合并与防幻觉规则由系统固定维护。修改后需重新解析才能影响已有内容。
                    </p>
                  </div>
                  <div className={styles.field}>
                    <label htmlFor="kb-wiki-extraction">Wiki 提取重点（可选）</label>
                    <textarea
                      id="kb-wiki-extraction"
                      maxLength={4000}
                      value={wikiExtractionInstructions}
                      onChange={(event) => setWikiExtractionInstructions(event.target.value)}
                      placeholder="例如：重点识别产品、版本、组织、负责人和关键技术概念…"
                    />
                    <p className={styles.configHint}>
                      说明应重点识别的领域实体与概念，不会替换系统的 JSON 与引用协议。
                    </p>
                  </div>
                  <div className={styles.field}>
                    <label htmlFor="kb-wiki-pages">单次最大页面数（可选）</label>
                    <input
                      id="kb-wiki-pages"
                      type="number"
                      min={0}
                      max={1000}
                      value={wikiMaxPages}
                      onChange={(event) => setWikiMaxPages(event.target.value)}
                      placeholder="0"
                    />
                    <p className={styles.configHint}>
                      每次摄入最多创建/更新的 Wiki 页面数，0 表示不限制；留空使用平台默认。
                    </p>
                  </div>
                </fieldset>
              ) : null}
              <div className={styles.dialogActions}>
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => setShowCreate(false)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={createDisabled}
                  onClick={() => void submit()}
                >
                  {creating ? "创建中…" : "创建"}
                </button>
              </div>
            </div>
          </div>
          </KnowledgeDrawerLayer>
        ) : null}
    </section>
  );
}
