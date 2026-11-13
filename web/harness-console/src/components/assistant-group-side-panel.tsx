"use client";

import { useId, useRef, useState } from "react";

import type {
  AssistantGroup,
  AssistantGroupLibrary,
  GroupLibraryDocument,
  GroupLibraryKnowledgeBase,
  GroupMessage,
} from "../lib/assistant-client";
import type { StudioKnowledgeBase } from "../lib/studio-client";
import { resolveDownloadUrl } from "../lib/assistant-download-url";
import { groupTimeLabel } from "../lib/group-chat-time";
import { ChatHistoryList } from "./chat-history-list";
import styles from "./assistant-group-side-panel.module.css";

export const GROUP_SIDE_TABS = [
  { key: "history", label: "聊天记录" },
  { key: "activity", label: "动态" },
  { key: "tasks", label: "任务" },
  { key: "library", label: "资料库" },
] as const;
export type GroupSideTab = typeof GROUP_SIDE_TABS[number]["key"];

const STATUS_LABEL: Record<GroupMessage["status"], string> = {
  running: "运行中",
  waiting: "等待确认",
  completed: "已完成",
  failed: "失败",
  stopped: "已停止",
};

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

type AssistantGroupSidePanelProps = {
  group: AssistantGroup;
  messages: GroupMessage[];
  /** 上传者本人或群创建者才能删除资料库文件。 */
  currentUserId?: string;
  isCreator?: boolean;
  tab: GroupSideTab;
  onTabChange: (tab: GroupSideTab) => void;
  onClose: () => void;
  /** 打开群信息（成员、改名、解散）；与单助理侧栏的详情入口对齐。 */
  onOpenInfo?: () => void;
  library: AssistantGroupLibrary | null;
  libraryLoading: boolean;
  libraryError: string | null;
  availableKnowledgeBases: StudioKnowledgeBase[];
  libraryBusy: boolean;
  onUpload: (file: File) => void;
  onDeleteDocument: (document: GroupLibraryDocument) => void;
  onBind: (reference: string) => void;
  onUnbind: (base: GroupLibraryKnowledgeBase) => void;
};

/**
 * 群右侧栏：动态 / 任务 / 资料库三个 tab，形态与 1:1 助理侧栏一致。
 * 动态与任务都从本群消息推导，资料库走群级接口。
 */
export function AssistantGroupSidePanel({
  group,
  messages,
  currentUserId = "",
  isCreator = false,
  tab,
  onTabChange,
  onClose,
  onOpenInfo,
  library,
  libraryLoading,
  libraryError,
  availableKnowledgeBases,
  libraryBusy,
  onUpload,
  onDeleteDocument,
  onBind,
  onUnbind,
}: AssistantGroupSidePanelProps) {
  const id = useId();
  const fileInput = useRef<HTMLInputElement | null>(null);
  const [bindReference, setBindReference] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const recent = [...messages].slice(-12).reverse();
  const runs = messages.filter(
    (message) => message.runId || message.status === "running" || message.status === "waiting",
  ).reverse();
  const bound = new Set((library?.knowledgeBases ?? []).map((base) => base.reference));
  const bindable = availableKnowledgeBases.filter((base) => !bound.has(base.reference));

  return (
    <aside className={styles.panel} aria-label="群右侧栏">
      <header className={styles.header}>
        <h2 className={styles.title} title={group.name}>{group.name}</h2>
        {onOpenInfo && (
          <button
            type="button"
            className={styles.infoButton}
            aria-label="群信息"
            title="群信息"
            onClick={onOpenInfo}
          >
            群信息
          </button>
        )}
        <button type="button" className={styles.close} aria-label="关闭群右侧栏" title="关闭" onClick={onClose}>
          ×
        </button>
      </header>
      <div className={styles.tabs} role="tablist" aria-label="群侧栏">
        {GROUP_SIDE_TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`${id}-${key}`}
            aria-selected={tab === key}
            aria-controls={`${id}-panel`}
            tabIndex={tab === key ? 0 : -1}
            onClick={() => onTabChange(key)}
            onKeyDown={(event) => {
              if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
              event.preventDefault();
              const at = GROUP_SIDE_TABS.findIndex((item) => item.key === key);
              const next = GROUP_SIDE_TABS[
                (at + (event.key === "ArrowRight" ? 1 : GROUP_SIDE_TABS.length - 1)) % GROUP_SIDE_TABS.length
              ].key;
              onTabChange(next);
              document.getElementById(`${id}-${next}`)?.focus();
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        className={styles.content}
        role="tabpanel"
        id={`${id}-panel`}
        aria-labelledby={`${id}-${tab}`}
      >
        {tab === "history" && (
          <ChatHistoryList
            items={messages.map((message) => ({
              id: message.messageId,
              author: message.authorName,
              text: message.text || STATUS_LABEL[message.status],
              time: groupTimeLabel(message.createdAt),
            }))}
            emptyText="这个群还没有聊天记录"
            searchPlaceholder="搜索群聊记录"
            label="群聊天记录"
          />
        )}

        {tab === "activity" && (
          <section className={styles.section} aria-label="群动态">
            {recent.length === 0 ? (
              <p className={styles.empty}>还没有活动</p>
            ) : (
              <ul className={styles.list}>
                {recent.map((message) => (
                  <li key={message.messageId} className={styles.activityItem}>
                    <span className={styles.activityName}>{message.authorName}</span>
                    <span className={styles.activityText}>
                      {message.text ? message.text.slice(0, 60) : STATUS_LABEL[message.status]}
                    </span>
                    <time className={styles.time} dateTime={message.createdAt}>
                      {groupTimeLabel(message.createdAt)}
                    </time>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {tab === "tasks" && (
          <section className={styles.section} aria-label="群任务">
            {runs.length === 0 ? (
              <p className={styles.empty}>这个群还没有机器人任务</p>
            ) : (
              <ul className={styles.list}>
                {runs.map((message) => (
                  <li key={message.messageId} className={styles.taskItem}>
                    <div className={styles.taskHead}>
                      <span className={styles.activityName}>{message.authorName}</span>
                      <span className={`${styles.status} ${styles[`status_${message.status}`] ?? ""}`}>
                        {STATUS_LABEL[message.status]}
                      </span>
                    </div>
                    <p className={styles.taskText}>{message.text.slice(0, 120) || "（没有文字输出）"}</p>
                    <time className={styles.time} dateTime={message.createdAt}>
                      {groupTimeLabel(message.createdAt)}
                    </time>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}

        {tab === "library" && (
          <section className={styles.section} aria-label="群资料库">
            {libraryLoading && <p className={styles.empty} role="status">正在读取资料库…</p>}
            {libraryError && <p className={styles.error} role="alert">{libraryError}</p>}
            <div className={styles.groupHeading}>
              <h3>文件</h3>
              <button
                type="button"
                className={styles.secondaryButton}
                disabled={libraryBusy}
                onClick={() => fileInput.current?.click()}
              >
                {libraryBusy ? "上传中…" : "上传文件"}
              </button>
              <input
                ref={fileInput}
                type="file"
                className={styles.fileInput}
                aria-label="选择要上传到群资料库的文件"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (file) onUpload(file);
                }}
              />
            </div>
            {library && library.documents.length === 0 ? (
              <p className={styles.empty}>群资料库还没有文件。上传后可以随消息发送。</p>
            ) : (
              <ul className={styles.list}>
                {(library?.documents ?? []).map((document) => {
                  // 服务端给的是 /v1/...，浏览器侧只代理 /api/...，统一在这里换算。
                  const downloadUrl = resolveDownloadUrl(document.downloadUrl);
                  return (
                    <li key={document.documentId} className={styles.docItem}>
                      <div className={styles.docMain}>
                        {downloadUrl ? (
                          <a
                            className={styles.docName}
                            href={downloadUrl}
                            target="_blank"
                            rel="noreferrer"
                            title={`下载 ${document.name}`}
                          >
                            {document.name}
                          </a>
                        ) : (
                          <span className={styles.docName} title={document.name}>{document.name}</span>
                        )}
                        <span className={styles.time}>
                          {formatBytes(document.sizeBytes)} · {document.uploadedByName} · {groupTimeLabel(document.createdAt)}
                        </span>
                      </div>
                      {(isCreator || document.uploadedBy === currentUserId) && (
                        <button
                          type="button"
                          className={`${styles.secondaryButton} ${confirmDelete === document.documentId ? styles.dangerButton : ""}`}
                          disabled={libraryBusy}
                          onClick={() => {
                            if (confirmDelete === document.documentId) {
                              setConfirmDelete(null);
                              onDeleteDocument(document);
                            } else {
                              setConfirmDelete(document.documentId);
                            }
                          }}
                          onBlur={() => setConfirmDelete((current) => (
                            current === document.documentId ? null : current
                          ))}
                        >
                          {confirmDelete === document.documentId ? "确认删除？" : "删除"}
                        </button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}

            <div className={styles.groupHeading}>
              <h3>知识库</h3>
            </div>
            {library && library.knowledgeBases.length === 0 ? (
              <p className={styles.empty}>还没有绑定知识库。绑定后，群里 @ 机器人时会带上它的内容。</p>
            ) : (
              <ul className={styles.list}>
                {(library?.knowledgeBases ?? []).map((base) => (
                  <li key={base.reference} className={styles.docItem}>
                    <div className={styles.docMain}>
                      <span className={styles.docName}>{base.name}</span>
                      <span className={styles.time}>
                        {base.type} · {base.engine} · {base.sourceCount} 个来源
                      </span>
                    </div>
                    <button
                      type="button"
                      className={styles.secondaryButton}
                      disabled={libraryBusy}
                      onClick={() => onUnbind(base)}
                    >
                      解绑
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div className={styles.bindRow}>
              <select
                value={bindReference}
                aria-label="选择要绑定的知识库"
                disabled={libraryBusy || bindable.length === 0}
                onChange={(event) => setBindReference(event.target.value)}
              >
                <option value="">
                  {bindable.length ? "选择知识库…" : "没有可绑定的知识库"}
                </option>
                {bindable.map((base) => (
                  <option key={base.reference} value={base.reference}>
                    {base.displayName}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className={styles.secondaryButton}
                disabled={!bindReference || libraryBusy}
                onClick={() => {
                  onBind(bindReference);
                  setBindReference("");
                }}
              >
                绑定
              </button>
            </div>
          </section>
        )}
      </div>
    </aside>
  );
}
