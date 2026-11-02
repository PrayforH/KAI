"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { SecretInput } from "./secret-input";
import { useConfirmationDialog } from "./confirmation-dialog";
import styles from "./assistant-vault.module.css";

type VaultCategory = "login" | "card" | "contact";

type VaultItem = {
  itemId: string;
  category: VaultCategory;
  title: string;
  reference: string;
  hasSecret: boolean;
  last4: string | null;
  revision: number;
  createdAt: string;
  updatedAt: string;
};

const CATEGORIES: Array<{ id: "all" | VaultCategory; label: string }> = [
  { id: "all", label: "全部" },
  { id: "login", label: "登录密钥" },
  { id: "card", label: "卡片" },
  { id: "contact", label: "联系信息" },
];

const CATEGORY_LABEL: Record<VaultCategory, string> = {
  login: "登录密钥",
  card: "卡片",
  contact: "联系信息",
};

type Draft = {
  itemId: string | null;
  category: VaultCategory;
  title: string;
  revision: number;
};

/**
 * 助理资料库：登录密钥、卡片与联系信息都在本人名下，按 Bot 授权后才可用。
 * 列表只有脱敏元数据——值不进模型、不进聊天、不进日志。
 */
export function AssistantVault() {
  const [items, setItems] = useState<VaultItem[]>([]);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [category, setCategory] = useState<"all" | VaultCategory>("all");
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [vault, bots] = await Promise.all([
        assistantRequest<VaultItem[]>("vault"),
        assistantRequest<Specialist[]>("specialists"),
      ]);
      setItems(vault);
      setSpecialists(bots.filter(item => item.status !== "archived"));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取资料库失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items.filter((item) => {
      if (category !== "all" && item.category !== category) return false;
      if (!needle) return true;
      return `${item.title} ${CATEGORY_LABEL[item.category]}`.toLowerCase().includes(needle);
    });
  }, [items, category, query]);

  async function remove(item: VaultItem) {
    const confirmed = await requestConfirmation({
      title: "删除这条资料？",
      description: "删除后这条资料立即失效，已经授权给 Bot 的引用也会作废。",
      confirmLabel: "删除资料",
      context: <span>标题：<code>{item.title}</code></span>,
      tone: "danger",
    });
    if (!confirmed) return;
    try {
      await assistantRequest(
        `vault/${encodeURIComponent(item.itemId)}`,
        "DELETE",
        { confirmTitle: item.title },
      );
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "删除失败");
    }
  }

  async function toggleGrant(item: VaultItem, specialist: Specialist) {
    const granted = Boolean((specialist.config as { [key: string]: unknown } | undefined)?.[
      `credential_ref:${item.title}`
    ]);
    try {
      await assistantRequest(`vault/${encodeURIComponent(item.itemId)}/grant`, "POST", {
        specialistId: specialist.specialistId,
        granted: !granted,
        expectedRevision: specialist.revision,
      });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "授权失败");
    }
  }

  return (
    <div className={styles.vault}>
      <div className={styles.toolbar}>
        <div className={styles.categories} role="tablist" aria-label="资料分类">
          {CATEGORIES.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={category === item.id}
              data-active={category === item.id ? "true" : undefined}
              onClick={() => setCategory(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <input
          className={styles.search}
          value={query}
          placeholder="搜索资料"
          aria-label="搜索资料"
          onChange={event => setQuery(event.target.value)}
        />
        <button
          type="button"
          className={styles.create}
          onClick={() => setDraft({ itemId: null, category: "login", title: "", revision: 1 })}
        >
          新建资料
        </button>
      </div>
      {error && <p className={styles.error} role="alert">{error}</p>}
      {loading ? (
        <p className={styles.empty}>正在读取资料库…</p>
      ) : visible.length === 0 ? (
        <p className={styles.empty}>这一类还没有资料。资料只归你所有，授权后对应的 Bot 才能用。</p>
      ) : (
        <ul className={styles.list}>
          {visible.map((item) => (
            <li key={item.itemId} className={styles.item}>
              <div className={styles.itemMain}>
                <strong>{item.title}</strong>
                <span className={styles.badge}>{CATEGORY_LABEL[item.category]}</span>
                {item.last4 && <span className={styles.badge}>末四位 {item.last4}</span>}
                {item.hasSecret ? (
                  <span className={styles.badge}>已保存</span>
                ) : (
                  <span className={styles.badge} data-muted="true">只有标题</span>
                )}
              </div>
              <div className={styles.grants}>
                {specialists.length === 0 ? (
                  <span className={styles.grantHint}>还没有可授权的 Bot</span>
                ) : specialists.map((specialist) => {
                  const granted = Boolean((specialist.config as { [key: string]: unknown } | undefined)?.[
                    `credential_ref:${item.title}`
                  ]);
                  return (
                    <button
                      key={specialist.specialistId}
                      type="button"
                      className={styles.grant}
                      data-granted={granted ? "true" : undefined}
                      onClick={() => void toggleGrant(item, specialist)}
                    >
                      {granted ? "收回 " : "授权给 "}{specialist.name}
                    </button>
                  );
                })}
              </div>
              <div className={styles.actions}>
                <button
                  type="button"
                  onClick={() => setDraft({
                    itemId: item.itemId,
                    category: item.category,
                    title: item.title,
                    revision: item.revision,
                  })}
                >
                  编辑
                </button>
                <button type="button" className={styles.danger} onClick={() => void remove(item)}>
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {draft && (
        <VaultEditor
          draft={draft}
          onClose={() => setDraft(null)}
          onSaved={() => {
            setDraft(null);
            void load();
          }}
        />
      )}
      {confirmationDialog}
    </div>
  );
}

function VaultEditor({
  draft,
  onClose,
  onSaved,
}: {
  draft: Draft;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(draft.title);
  const [category, setCategory] = useState<VaultCategory>(draft.category);
  const [value, setValue] = useState("");
  const [last4, setLast4] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function save() {
    setBusy(true);
    setError("");
    try {
      if (draft.itemId) {
        await assistantRequest(`vault/${encodeURIComponent(draft.itemId)}`, "PUT", {
          title: title.trim() || draft.title,
          ...(value ? { value } : {}),
          ...(last4 ? { last4 } : {}),
          expectedRevision: draft.revision,
        });
      } else {
        await assistantRequest("vault", "POST", {
          category,
          title: title.trim() || "未命名资料",
          ...(value ? { value } : {}),
          ...(last4 ? { last4 } : {}),
        });
      }
      onSaved();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败");
      setBusy(false);
    }
  }

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-label="编辑资料">
      <div className={styles.panel}>
        <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>×</button>
        <label className={styles.field}>
          <span>分类</span>
          <select
            value={category}
            aria-label="分类"
            disabled={Boolean(draft.itemId)}
            onChange={event => setCategory(event.target.value as VaultCategory)}
          >
            {CATEGORIES.filter(item => item.id !== "all").map(item => (
              <option key={item.id} value={item.id}>{item.label}</option>
            ))}
          </select>
        </label>
        <label className={styles.field}>
          <span>标题</span>
          <input value={title} maxLength={120} aria-label="标题" onChange={event => setTitle(event.target.value)} />
        </label>
        <label className={styles.field}>
          <span>{category === "card" ? "安全令牌（可选）" : "值（可选）"}</span>
          <SecretInput
            value={value}
            onChange={event => setValue(event.target.value)}
            placeholder={draft.itemId ? "留空表示不改动已保存的值" : "只保存你自己填写的值"}
          />
        </label>
        {category === "card" && (
          <label className={styles.field}>
            <span>末四位（可选）</span>
            <input value={last4} maxLength={4} aria-label="末四位" onChange={event => setLast4(event.target.value)} />
            <small className={styles.hint}>只保存安全令牌与末四位；不保存 CVV，也不做付款。</small>
          </label>
        )}
        {error && <p className={styles.error} role="alert">{error}</p>}
        <button type="button" className={styles.primary} disabled={busy} onClick={() => void save()}>
          {busy ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}
