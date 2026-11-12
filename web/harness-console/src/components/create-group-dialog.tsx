"use client";

import { useId, useState } from "react";

import {
  addAssistantGroupMember,
  createAssistantGroup,
  type Specialist,
} from "../lib/assistant-client";
import styles from "./create-group-dialog.module.css";

type Props = {
  /** 侧栏已加载并按同一规则过滤好的子助理（排除已归档与个人主线程）。 */
  specialists: Specialist[];
  /** 侧栏名单是否已就绪；未就绪前不展示空态、禁止创建。 */
  ready: boolean;
  /** 侧栏名单加载失败时的明确错误。 */
  error: string | null;
  onRetry: () => void;
  onClose: () => void;
  onCreated: (groupId: string) => void;
};

/** 从助理主界面发起建群：名称必填，勾选的子助理随创建一起入群。 */
export function CreateGroupDialog({
  specialists,
  ready,
  error,
  onRetry,
  onClose,
  onCreated,
}: Props) {
  const titleId = useId();
  const [name, setName] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const toggle = (specialistId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(specialistId)) next.delete(specialistId);
      else next.add(specialistId);
      return next;
    });
  };

  const submit = async () => {
    // 建群不依赖花名册：名单还在加载或加载失败时也能先建成“创建者+主助理”的群。
    if (!name.trim() || busy) return;
    setBusy(true);
    setSubmitError(null);
    try {
      const group = await createAssistantGroup(name.trim());
      // 群已经建好就要进群；个别成员邀请失败不能让整次创建看起来失败，
      // 否则重试会再建一个群。失败的人进群后在花名册里再拉即可。
      await Promise.allSettled(
        [...selected].map((specialistId) =>
          addAssistantGroupMember(group.groupId, specialistId),
        ),
      );
      onCreated(group.groupId);
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "创建群聊失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className={styles.panel}>
        <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>
          ×
        </button>
        <h2 id={titleId}>创建群聊</h2>
        <p className={styles.hint}>
          创建者和主助理默认在群里；勾选的子助理会一起加入，之后还能在群里拉人或移出。
        </p>
        <label className={styles.field}>
          群名称
          <input
            autoFocus
            value={name}
            maxLength={40}
            placeholder="例如：编码协同"
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void submit();
            }}
          />
        </label>
        <fieldset className={styles.members}>
          <legend>拉入子助理（可多选）</legend>
          {!ready ? (
            <span className={styles.empty}>
              {error
                ? "子助理加载失败，请点下方“重试”；也可以先建群，进群后再拉人。"
                : "正在加载子助理…也可以先建群，进群后再拉人。"}
            </span>
          ) : specialists.length === 0 ? (
            <span className={styles.empty}>还没有可拉入的子助理，可先只建创建者+主助理的群。</span>
          ) : (
            specialists.map((item) => (
              <label key={item.specialistId} className={styles.member}>
                <input
                  type="checkbox"
                  checked={selected.has(item.specialistId)}
                  onChange={() => toggle(item.specialistId)}
                />
                <span>{item.name}</span>
              </label>
            ))
          )}
        </fieldset>
        {!ready && error && (
          <p className={styles.error} role="alert">
            子助理加载失败：{error}
          </p>
        )}
        {submitError && <p className={styles.error} role="alert">{submitError}</p>}
        <div className={styles.actions}>
          {!ready && error && (
            <button type="button" className={styles.secondary} onClick={onRetry}>
              重试
            </button>
          )}
          <button type="button" className={styles.secondary} onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={!name.trim() || busy}
            onClick={() => void submit()}
          >
            {busy ? "正在创建…" : "创建群聊"}
          </button>
        </div>
      </div>
    </div>
  );
}
