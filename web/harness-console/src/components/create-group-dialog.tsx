"use client";

import { useEffect, useId, useState } from "react";

import {
  addAssistantGroupMember,
  assistantRequest,
  createAssistantGroup,
  type Specialist,
} from "../lib/assistant-client";
import styles from "./create-group-dialog.module.css";

const PERSONAL_THREAD = "kai-personal-assistant-v1";

type Props = {
  onClose: () => void;
  onCreated: (groupId: string) => void;
};

/** 从助理主界面发起建群：名称必填，勾选的子助理随创建一起入群。 */
export function CreateGroupDialog({ onClose, onCreated }: Props) {
  const titleId = useId();
  const [name, setName] = useState("");
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setReady(false);
    setLoadError(null);
    void assistantRequest<Specialist[]>("specialists")
      .then((list) => {
        if (cancelled) return;
        setSpecialists(
          list.filter(
            (item) =>
              item.status !== "archived" &&
              item.config?.conversation_thread_id !== PERSONAL_THREAD,
          ),
        );
        setReady(true);
      })
      .catch((reason) => {
        if (!cancelled) {
          setLoadError(reason instanceof Error ? reason.message : "子助理加载失败");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const toggle = (specialistId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(specialistId)) next.delete(specialistId);
      else next.add(specialistId);
      return next;
    });
  };

  const submit = async () => {
    if (!name.trim() || busy || !ready) return;
    setBusy(true);
    setError(null);
    try {
      const group = await createAssistantGroup(name.trim());
      for (const specialistId of selected) {
        await addAssistantGroupMember(group.groupId, specialistId);
      }
      onCreated(group.groupId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建群聊失败");
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
          {loading ? (
            <span className={styles.empty}>正在加载子助理…</span>
          ) : !ready ? (
            <span className={styles.empty}>子助理加载失败，请点下方“重试”。</span>
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
        {loadError && (
          <p className={styles.error} role="alert">
            子助理加载失败：{loadError}
          </p>
        )}
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.actions}>
          {!loading && !ready && (
            <button
              type="button"
              className={styles.secondary}
              onClick={() => setReloadKey((key) => key + 1)}
            >
              重试
            </button>
          )}
          <button type="button" className={styles.secondary} onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={!name.trim() || busy || !ready}
            onClick={() => void submit()}
          >
            {busy ? "正在创建…" : loading ? "加载中…" : "创建群聊"}
          </button>
        </div>
      </div>
    </div>
  );
}
