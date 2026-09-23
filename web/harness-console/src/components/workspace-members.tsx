"use client";
import { FeedbackToast } from "./feedback-toast";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { AuthUser, Membership } from "../lib/auth-session";
import styles from "./workspace-members.module.css";

type Role = Membership["role"];

type WorkspaceMember = {
  user: AuthUser & { disabled: boolean };
  membership: Membership & { created_at: string };
};

const ROLE_LABELS: Record<Role, string> = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  viewer: "只读成员",
};

function responseError(payload: unknown): string {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    payload.error &&
    typeof payload.error === "object" &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }
  return "成员角色未能保存。";
}

export function WorkspaceMembers({
  currentUserId,
  currentRole,
}: {
  currentUserId: string;
  currentRole: Role;
}) {
  const canManage = currentRole === "owner" || currentRole === "admin";
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [drafts, setDrafts] = useState<Record<string, Role>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    if (!canManage) return;
    try {
      const response = await fetch("/api/auth/members", { cache: "no-store" });
      const payload = (await response.json()) as unknown;
      if (!response.ok) throw new Error(responseError(payload));
      const next = payload as WorkspaceMember[];
      setMembers(next);
      setDrafts(
        Object.fromEntries(
          next.map((member) => [
            member.user.user_id,
            member.membership.role,
          ]),
        ),
      );
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "成员列表暂时不可用。");
    }
  }, [canManage]);

  useEffect(() => {
    void load();
  }, [load]);

  const ownerCount = useMemo(
    () => members.filter((member) => member.membership.role === "owner").length,
    [members],
  );

  async function save(member: WorkspaceMember) {
    const role = drafts[member.user.user_id];
    if (!role || role === member.membership.role) return;
    setBusy(member.user.user_id);
    setError("");
    setNotice("");
    try {
      const response = await fetch(
        `/api/auth/members/${encodeURIComponent(member.user.user_id)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role }),
        },
      );
      const payload = (await response.json()) as unknown;
      if (!response.ok) throw new Error(responseError(payload));
      const updated = payload as WorkspaceMember;
      setMembers((current) =>
        current.map((item) =>
          item.user.user_id === updated.user.user_id ? updated : item
        ),
      );
      setNotice(`${updated.user.display_name} 已调整为${ROLE_LABELS[role]}。`);
      if (updated.user.user_id === currentUserId) {
        window.location.reload();
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "成员角色未能保存。");
      setDrafts((current) => ({
        ...current,
        [member.user.user_id]: member.membership.role,
      }));
    } finally {
      setBusy("");
    }
  }

  if (!canManage) {
    return (
      <div className={styles.restricted}>
        <strong>成员角色由 Owner / Admin 管理</strong>
        <p>你当前是{ROLE_LABELS[currentRole]}。如需创建数据源或发布智能体，请联系工作区管理员调整角色。</p>
      </div>
    );
  }

  return (
    <div className={styles.panel}>
      <header>
        <div>
          <strong>{members.length} 位成员</strong>
          <span>{ownerCount} 位所有者 · 角色变更会写入审计日志 · 管理权限不包含查看成员的私人任务与智能体</span>
        </div>
        <button type="button" onClick={() => void load()}>刷新</button>
      </header>
      <FeedbackToast message={notice} onDismiss={() => setNotice("")} />
      <FeedbackToast message={error} tone="error" onDismiss={() => setError("")} />
      <div className={styles.list}>
        {members.map((member) => {
          const role = drafts[member.user.user_id] ?? member.membership.role;
          const protectedFromAdmin =
            currentRole === "admin" &&
            (member.membership.role === "owner" || member.membership.role === "admin");
          const roleOptions: readonly Role[] =
            currentRole === "owner"
              ? ["owner", "admin", "member", "viewer"]
              : protectedFromAdmin
                ? [member.membership.role]
                : ["member", "viewer"];
          return (
            <article key={member.user.user_id}>
              <span className={styles.avatar} aria-hidden="true">
                {member.user.display_name.slice(0, 1).toUpperCase()}
              </span>
              <div className={styles.identity}>
                <strong>
                  {member.user.display_name}
                  {member.user.user_id === currentUserId && <small>你</small>}
                </strong>
                <span>{member.user.email}</span>
              </div>
              <select
                aria-label={`${member.user.display_name}的角色`}
                value={role}
                disabled={protectedFromAdmin || busy === member.user.user_id}
                onChange={(event) =>
                  setDrafts((current) => ({
                    ...current,
                    [member.user.user_id]: event.target.value as Role,
                  }))
                }
              >
                {roleOptions.map((option) => (
                  <option key={option} value={option}>{ROLE_LABELS[option]}</option>
                ))}
              </select>
              <button
                type="button"
                className={styles.save}
                disabled={
                  protectedFromAdmin ||
                  busy === member.user.user_id ||
                  role === member.membership.role
                }
                onClick={() => void save(member)}
              >
                {busy === member.user.user_id ? "保存中" : "保存"}
              </button>
            </article>
          );
        })}
      </div>
      <footer>
        <span>Owner：成员与租户全权限</span>
        <span>Admin：发布、部署和数据源管理</span>
        <span>Member：编辑与预览</span>
        <span>Viewer：只读</span>
      </footer>
    </div>
  );
}
