"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { assistantRequest, updateAssistantGroup, type AssistantGroup } from "../lib/assistant-client";
import styles from "./bot-rail-menu.module.css";

/** 群名上限，和后端保持一致；输入框 maxLength 与保存前截断用同一个常量。 */
const GROUP_NAME_MAX_LENGTH = 80;

/** 群名里的换行/控制字符会让左侧列表标题塌成两行，保存前统一剥掉。 */
function cleanGroupName(value: string): string {
  return value.replace(/[\u0000-\u001F\u007F]/g, "").trim().slice(0, GROUP_NAME_MAX_LENGTH);
}

type MenuMode = "main" | "profile" | "delete";

/**
 * 群聊列表项的操作入口：仅创建者可见。群资料（名称/描述）与解散群都收在这里；
 * 删除/解散需输入群名二次确认，资料保存走现有 PATCH /groups/{id}，不新增后端。
 * 群信息面板删除后，这里是群资料与解散群的唯一入口。
 */
export function GroupRailMenu({
  group,
  onDeleted,
}: {
  group: AssistantGroup;
  onDeleted: () => void;
}) {
  const anchor = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [mode, setMode] = useState<MenuMode>("main");
  const [confirmName, setConfirmName] = useState("");
  const [profileName, setProfileName] = useState(group.name);
  const [profileDescription, setProfileDescription] = useState(group.description ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function close() {
    if (busy) return;
    setPosition(null);
    setMode("main");
    setConfirmName("");
    setError("");
    anchor.current?.focus();
  }

  /** 进入群资料编辑：用当前群资料重置草稿，避免上一次取消留下的输入。 */
  function openProfile() {
    setProfileName(group.name);
    setProfileDescription(group.description ?? "");
    setError("");
    setMode("profile");
  }

  useEffect(() => {
    if (!position) return;
    if (!busy) {
      if (mode === "profile") nameInput.current?.focus();
      else panel.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    }
    function outside(event: PointerEvent) {
      if (!panel.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) {
        if (!busy) close();
      }
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) close();
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [position, busy, mode]);

  const trimmedProfileName = cleanGroupName(profileName);
  const canSaveProfile = trimmedProfileName.length > 0 && !busy;

  async function saveProfile() {
    if (!canSaveProfile) return;
    setBusy(true);
    setError("");
    try {
      await updateAssistantGroup(group.groupId, {
        name: trimmedProfileName,
        // 留空即清空描述，服务端把它当 null 处理。
        description: profileDescription.trim() || null,
      });
      setPosition(null);
      setMode("main");
      // 左侧列表和群标题都在监听这个事件，保存后立即刷新。
      window.dispatchEvent(new CustomEvent("kai:groups-changed"));
    } catch (cause) {
      // 失败留在菜单里，输入不丢，错误就地显示。
      setError(cause instanceof Error ? cause.message : "保存群资料失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (confirmName.trim() !== group.name) return;
    setBusy(true);
    setError("");
    try {
      // 群解散的唯一入口，不新增后端端点。
      await assistantRequest<void>(`groups/${encodeURIComponent(group.groupId)}`, "DELETE");
      setPosition(null);
      onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "解散群聊失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="bot-rail-menu-trigger"
        aria-label={`${group.name} 的更多操作`}
        aria-haspopup="menu"
        aria-expanded={Boolean(position)}
        onClick={() => {
          if (position) {
            close();
            return;
          }
          const rect = anchor.current!.getBoundingClientRect();
          setMode("main");
          setError("");
          setConfirmName("");
          setPosition({
            left: Math.max(8, Math.min(rect.left, window.innerWidth - 280)),
            top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 320)),
          });
        }}
      >
        ···
      </button>
      {position &&
        createPortal(
          <div
            ref={panel}
            className={styles.menu}
            style={position}
            role={mode === "delete" ? "alertdialog" : mode === "profile" ? "dialog" : "menu"}
            aria-label={
              mode === "delete"
                ? `解散 ${group.name}`
                : mode === "profile"
                  ? `编辑 ${group.name} 的群资料`
                  : `${group.name} 的更多操作`
            }
          >
            {mode === "main" && (
              <>
                <button role="menuitem" type="button" onClick={openProfile}>
                  群资料…
                </button>
                <hr />
                <button
                  role="menuitem"
                  type="button"
                  className={styles.danger}
                  onClick={() => {
                    setError("");
                    setMode("delete");
                  }}
                >
                  删除群聊…
                </button>
              </>
            )}
            {mode === "profile" && (
              <>
                <strong>群资料</strong>
                <label htmlFor="group-profile-name">群名称</label>
                <input
                  id="group-profile-name"
                  ref={nameInput}
                  value={profileName}
                  aria-label="群名称"
                  maxLength={GROUP_NAME_MAX_LENGTH}
                  disabled={busy}
                  onChange={(event) => setProfileName(event.target.value)}
                />
                <label htmlFor="group-profile-description">群描述</label>
                <textarea
                  id="group-profile-description"
                  className={styles.textarea}
                  value={profileDescription}
                  aria-label="群描述"
                  rows={3}
                  disabled={busy}
                  placeholder="选填，一句话说明这个群在做什么"
                  onChange={(event) => setProfileDescription(event.target.value)}
                />
                <div className={styles.actions}>
                  <button type="button" disabled={busy} onClick={close}>取消</button>
                  <button
                    type="button"
                    disabled={!canSaveProfile}
                    className={styles.primary}
                    onClick={() => void saveProfile()}
                  >
                    {busy ? "保存中…" : "保存"}
                  </button>
                </div>
              </>
            )}
            {mode === "delete" && (
              <>
                <strong>解散“{group.name}”？</strong>
                <p>解散后成员、消息、表情与资料库都会被清空，无法恢复。</p>
                <p>输入群名「{group.name}」确认解散：</p>
                <input
                  value={confirmName}
                  aria-label="输入群名确认解散"
                  placeholder={group.name}
                  disabled={busy}
                  onChange={(event) => setConfirmName(event.target.value)}
                />
                <div className={styles.actions}>
                  <button type="button" disabled={busy} onClick={close}>取消</button>
                  <button
                    type="button"
                    disabled={busy || confirmName.trim() !== group.name}
                    className={styles.danger}
                    onClick={() => void remove()}
                  >
                    {busy ? "解散中…" : "确认解散"}
                  </button>
                </div>
              </>
            )}
            {busy && mode === "main" && <p role="status">正在删除…</p>}
            {error && <p className={styles.error} role="alert">{error}</p>}
          </div>,
          document.body,
        )}
    </>
  );
}
