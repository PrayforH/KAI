"use client";

import { useMemo, useState } from "react";

import styles from "./chat-history-list.module.css";

export type ChatHistoryItem = {
  id: string;
  /** 发送人展示名：我 / 助理名 / 群成员名。 */
  author: string;
  text: string;
  /** 已格式化好的时间，避免两个入口各自实现一套时间口径。 */
  time: string;
  meta?: string;
};

type ChatHistoryListProps = {
  items: readonly ChatHistoryItem[];
  loading?: boolean;
  error?: string | null;
  emptyText?: string;
  searchPlaceholder?: string;
  label?: string;
  onLocate?: (id: string) => void;
};

/**
 * 聊天记录列表：群右侧栏与助理右侧栏共用。
 * 只做搜索与展示；数据由调用方按各自接口读取，定位动作由调用方提供。
 */
export function ChatHistoryList({
  items,
  loading = false,
  error = null,
  emptyText = "还没有聊天记录",
  searchPlaceholder = "搜索聊天记录",
  label = "聊天记录",
  onLocate,
}: ChatHistoryListProps) {
  const [query, setQuery] = useState("");
  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return items;
    return items.filter((item) =>
      `${item.author} ${item.text}`.toLowerCase().includes(keyword),
    );
  }, [items, query]);
  // 最新一条在上，和聊天记录抽屉的口径一致。
  const ordered = useMemo(() => [...filtered].reverse(), [filtered]);

  return (
    <section className={styles.root} aria-label={label}>
      <label className={styles.search}>
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <circle cx="9" cy="9" r="6" />
          <path d="m14 14 4 4" />
        </svg>
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={searchPlaceholder}
          aria-label={searchPlaceholder}
        />
      </label>
      {loading && <p className={styles.empty} role="status">正在读取聊天记录…</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
      {!loading && !error && items.length === 0 && <p className={styles.empty}>{emptyText}</p>}
      {!loading && !error && items.length > 0 && ordered.length === 0 && (
        <p className={styles.empty}>没有匹配的聊天记录</p>
      )}
      {!loading && !error && ordered.length > 0 && (
        <ul className={styles.list}>
          {ordered.map((item) => (
            <li key={item.id} className={styles.item}>
              <div className={styles.meta}>
                <strong>{item.author}</strong>
                <time>{item.time}</time>
                {onLocate && (
                  <button type="button" className={styles.locate} onClick={() => onLocate(item.id)}>
                    在聊天中定位
                  </button>
                )}
              </div>
              {item.text && <p className={styles.text}>{item.text}</p>}
              {item.meta && <span className={styles.sub}>{item.meta}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
