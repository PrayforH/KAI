"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../auth-provider";
import {
  studioClient,
  type KnowledgeBaseType,
  type StudioKnowledgeBase,
} from "../../lib/studio-client";
import { KnowledgeMembersPanel } from "./knowledge-members-panel";
import styles from "./knowledge-console.module.css";

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
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [membersFor, setMembersFor] = useState<StudioKnowledgeBase | null>(null);

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

  const visible = useMemo(() => {
    if (filter === "mine" && user?.user_id) {
      return bases.filter((base) => base.createdBy === user.user_id);
    }
    return bases;
  }, [bases, filter, user?.user_id]);

  const createDisabled = creating || !reference.trim() || !displayName.trim();

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
      });
      setShowCreate(false);
      setNotice(`知识库「${created.displayName}」已创建`);
      setReference("");
      setDisplayName("");
      setDescription("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建知识库失败");
    } finally {
      setCreating(false);
    }
  }, [createDisabled, description, displayName, kbType, load, reference]);

  return (
    <section className={styles.content}>
        <div className={styles.hero}>
          <div className={styles.heroText}>
            <h1>知识库</h1>
            <p>统一管理 RAG、Wiki 与混合知识库；文档解析、切片与图谱由 WeKnora 提供</p>
          </div>
          {canManage ? (
            <button
              type="button"
              className={styles.primary}
              onClick={() => setShowCreate(true)}
            >
              新建知识库
            </button>
          ) : null}
        </div>

        <div className={styles.tabs}>
          <button
            type="button"
            className={`${styles.tab} ${filter === "all" ? styles.tabActive : ""}`}
            onClick={() => setFilter("all")}
          >
            全部 {bases.length}
          </button>
          <button
            type="button"
            className={`${styles.tab} ${filter === "mine" ? styles.tabActive : ""}`}
            onClick={() => setFilter("mine")}
          >
            我创建的{" "}
            {user?.user_id
              ? bases.filter((base) => base.createdBy === user.user_id).length
              : 0}
          </button>
        </div>

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
                    <h3 className={styles.cardTitle}>{base.displayName}</h3>
                  </div>
                  <p className={styles.cardDesc}>
                    {base.description || "暂无描述"}
                  </p>
                </Link>
                <div className={styles.cardMeta}>
                  <span className={styles.countBadge} title="文档数量">
                    <svg viewBox="0 0 20 20" aria-hidden="true">
                      <path d="M3.5 5.5h5l1.5 2h6.5v7h-13z" />
                    </svg>
                    {base.documentCount}
                  </span>
                  <span className={styles.cardRef}>{base.reference}</span>
                  <button
                    type="button"
                    className={styles.cardAction}
                    onClick={() => setMembersFor(base)}
                    title="管理该知识库的成员与权限"
                  >
                    成员管理
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}

        {membersFor ? (
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
        ) : null}

        {showCreate ? (
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
                选择知识库类型；WeKnora 负责解析、切片、向量与 Wiki 构建。
              </p>
              <div className={styles.typeRow}>
                {(Object.keys(KB_TYPE_LABELS) as KnowledgeBaseType[]).map((type) => (
                  <button
                    key={type}
                    type="button"
                    className={`${styles.typeCard} ${kbType === type ? styles.typeCardActive : ""}`}
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
                  onChange={(event) => setReference(event.target.value)}
                  placeholder="case-library"
                />
              </div>
              <div className={styles.field}>
                <label htmlFor="kb-name">名称</label>
                <input
                  id="kb-name"
                  value={displayName}
                  onChange={(event) => setDisplayName(event.target.value)}
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
        ) : null}
    </section>
  );
}
