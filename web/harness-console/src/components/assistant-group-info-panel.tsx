"use client";

import { useEffect, useState } from "react";

import type { AssistantGroup, Specialist } from "../lib/assistant-client";
import styles from "./assistant-group-info-panel.module.css";

const PRIMARY_MEMBER_ID = "primary";
const PERSONAL_THREAD = "kai-personal-assistant-v1";
const MAX_NAME = 80;
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
  specialists: Specialist[];
  onUpdate: (fields: GroupProfileUpdate) => Promise<boolean>;
  onAddMember: (specialistId: string) => void;
  onRemoveMember: (memberId: string, name: string) => void;
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
 * 右上角「⋯」里的群信息：群资料（改名 / 描述）、成员管理、资料库入口、
 * 解散群（仅创建者，需输入群名二次确认）。保存失败保留输入并给一行错误。
 */
export function AssistantGroupInfoPanel({
  group,
  isCreator,
  busy,
  error,
  specialists,
  onUpdate,
  onAddMember,
  onRemoveMember,
  onReplyAs,
  onOpenLibrary,
  onDissolve,
  onClose,
}: AssistantGroupInfoPanelProps) {
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description ?? "");
  const [memberToAdd, setMemberToAdd] = useState("");
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
  const seated = new Set(
    group.members
      .map((member) => member.specialistId)
      .filter((value): value is string => Boolean(value)),
  );
  const addableSpecialists = specialists.filter(
    (item) =>
      item.status !== "archived" &&
      !seated.has(item.specialistId) &&
      item.config?.conversation_thread_id !== PERSONAL_THREAD,
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
      const ok = await onUpdate({
        name: trimmed,
        description: description.trim() ? description : null,
      });
      if (!ok) setLocalError("保存失败，已保留你输入的内容");
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

      <section className={styles.section} aria-label="成员管理">
        <h4>成员管理</h4>
        <div className={styles.roster}>
          {group.members.map((member) => {
            const role = member.kind === "creator" ? "创建者" : member.kind === "primary" ? "主助理" : "子助理";
            return (
              <span key={member.memberId} className={styles.chip}>
                {member.name}
                {member.name !== role && <small>{role}</small>}
                {member.kind !== "creator" && (
                  <button
                    type="button"
                    title={`移出 ${member.name}`}
                    aria-label={`移出 ${member.name}`}
                    disabled={busy}
                    onClick={() => onRemoveMember(member.memberId, member.name)}
                  >
                    ×
                  </button>
                )}
              </span>
            );
          })}
        </div>
        <div className={styles.inviteRow}>
          <select
            value={memberToAdd}
            aria-label="选择要邀请的子助理"
            disabled={busy || addableSpecialists.length === 0}
            onChange={(event) => setMemberToAdd(event.target.value)}
          >
            <option value="">
              {addableSpecialists.length ? "选择子助理…" : "无可添加的子助理"}
            </option>
            {addableSpecialists.map((item) => (
              <option key={item.specialistId} value={item.specialistId}>
                {item.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={styles.secondaryButton}
            disabled={!memberToAdd || busy}
            onClick={() => {
              onAddMember(memberToAdd);
              setMemberToAdd("");
            }}
          >
            邀请
          </button>
        </div>
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
