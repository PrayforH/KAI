"use client";

import { useEffect, useState } from "react";

import type { AssistantGroup } from "../lib/assistant-client";
import styles from "./assistant-group-info-panel.module.css";

const PRIMARY_MEMBER_ID = "primary";
/** 服务端上限 40 个字（见 service.py 的群名校验），前端按同一口径拦。 */
const MAX_NAME = 40;
const MAX_DESCRIPTION = 500;

export type GroupProfileUpdate = {
  name?: string;
  description?: string | null;
};

type AssistantGroupInfoPanelProps = {
  group: AssistantGroup;
  isCreator: boolean;
  busy: boolean;
  error: string | null;
  /** 成功返回 null；失败返回可读文案（服务端原文优先）。 */
  onUpdate: (fields: GroupProfileUpdate) => Promise<string | null>;
  onReplyAs: (value: string) => void;
  onOpenLibrary: () => void;
  onDissolve: () => Promise<boolean>;
  onClose: () => void;
};

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

/**
 * 右上角「⋯」里的群信息：群资料（改名 / 描述）、接话人、资料库入口、
 * 解散群（仅创建者，需输入群名二次确认）。保存失败保留输入并给一行错误。
 * 成员管理不在这里：页头头像簇直接打开成员管理弹窗。
 */
export function AssistantGroupInfoPanel({
  group,
  isCreator,
  busy,
  error,
  onUpdate,
  onReplyAs,
  onOpenLibrary,
  onDissolve,
  onClose,
}: AssistantGroupInfoPanelProps) {
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description ?? "");
  const [saving, setSaving] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [dissolveStep, setDissolveStep] = useState<"idle" | "confirm">("idle");
  const [dissolveName, setDissolveName] = useState("");

  useEffect(() => {
    setName(group.name);
    setDescription(group.description ?? "");
  }, [group.name, group.description]);

  const creator = group.members.find((member) => member.kind === "creator");
  const activeSpecialists = group.members.filter(
    (member) => member.kind === "specialist" && member.status === "active",
  );
  const dirty = name !== group.name || description !== (group.description ?? "");
  const message = localError ?? error;

  const save = async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setLocalError("群名不能为空");
      return;
    }
    if (trimmed.length > MAX_NAME) {
      setLocalError(`群名最多 ${MAX_NAME} 个字`);
      return;
    }
    if (description.length > MAX_DESCRIPTION) {
      setLocalError(`群描述最多 ${MAX_DESCRIPTION} 个字`);
      return;
    }
    setLocalError(null);
    setSaving(true);
    try {
      const failure = await onUpdate({
        name: trimmed,
        description: description.trim() ? description : null,
      });
      // 服务端文案优先，拿不到才用通用兜底；无论哪种，输入都不清空。
      if (failure === null) setLocalError(null);
      else setLocalError(failure || "保存失败，已保留你输入的内容");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={styles.panel} role="dialog" aria-label="群信息">
      <header className={styles.header}>
        <h3>群信息</h3>
        <button type="button" className={styles.close} aria-label="关闭群信息" title="关闭" onClick={onClose}>
          ×
        </button>
      </header>

      <section className={styles.section} aria-label="群资料">
        <h4>群资料</h4>
        <label className={styles.field}>
          <span>群名</span>
          <input
            value={name}
            maxLength={MAX_NAME}
            disabled={!isCreator || busy || saving}
            aria-describedby={message ? "group-profile-error" : undefined}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span>群描述</span>
          <textarea
            value={description}
            maxLength={MAX_DESCRIPTION}
            rows={2}
            placeholder={isCreator ? "补一句这个群做什么用" : "暂无描述"}
            disabled={!isCreator || busy || saving}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <dl className={styles.meta}>
          <div><dt>创建者</dt><dd>{creator?.name ?? group.creatorUserId}</dd></div>
          <div><dt>创建时间</dt><dd>{formatDate(group.createdAt)}</dd></div>
        </dl>
        {isCreator && (
          <div className={styles.saveRow}>
            <button
              type="button"
              className={styles.primaryButton}
              disabled={!dirty || busy || saving}
              onClick={() => void save()}
            >
              {saving ? "保存中…" : "保存"}
            </button>
          </div>
        )}
        {message && (
          <p className={styles.error} role="alert" id="group-profile-error">{message}</p>
        )}
      </section>

      {/* 成员管理已改为页头头像簇直接打开的弹窗，面板里不再重复这一节。 */}
      <section className={styles.section} aria-label="接话人">
        <h4>接话人</h4>
        <label className={styles.replyAs}>
          未 @ 接话人
          <select
            value={group.replyAs ?? PRIMARY_MEMBER_ID}
            disabled={busy}
            onChange={(event) => onReplyAs(event.target.value)}
          >
            <option value={PRIMARY_MEMBER_ID}>
              主助理{group.primaryPresent ? "" : "（已移出）"}
            </option>
            {group.replyAs &&
              group.replyAs !== PRIMARY_MEMBER_ID &&
              !activeSpecialists.some((member) => member.memberId === group.replyAs) && (
                <option value={group.replyAs}>{group.replyAs}（已移出）</option>
              )}
            {activeSpecialists.map((member) => (
              <option key={member.memberId} value={member.memberId}>
                {member.name}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className={styles.section} aria-label="群资料库">
        <h4>群资料库</h4>
        <button type="button" className={styles.secondaryButton} onClick={onOpenLibrary}>
          打开资料库
        </button>
      </section>

      {isCreator && (
        <section className={`${styles.section} ${styles.dangerSection}`} aria-label="解散群聊">
          <h4>解散群聊</h4>
          {dissolveStep === "idle" ? (
            <>
              <p className={styles.dangerHint}>解散后成员、消息、表情与资料库都会被清空，无法恢复。</p>
              <button
                type="button"
                className={styles.dangerButton}
                onClick={() => setDissolveStep("confirm")}
              >
                解散群聊
              </button>
            </>
          ) : (
            <>
              <p className={styles.dangerHint}>输入群名「{group.name}」确认解散：</p>
              <div className={styles.dissolveRow}>
                <input
                  value={dissolveName}
                  aria-label="输入群名确认解散"
                  placeholder={group.name}
                  disabled={busy}
                  onChange={(event) => setDissolveName(event.target.value)}
                />
                <button
                  type="button"
                  className={styles.dangerButton}
                  disabled={busy || dissolveName.trim() !== group.name}
                  onClick={() => void onDissolve()}
                >
                  {busy ? "解散中…" : "确认解散"}
                </button>
                <button
                  type="button"
                  className={styles.secondaryButton}
                  disabled={busy}
                  onClick={() => {
                    setDissolveStep("idle");
                    setDissolveName("");
                  }}
                >
                  取消
                </button>
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
}
