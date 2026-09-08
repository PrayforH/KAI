"use client";

import { useCallback, useEffect, useState } from "react";
import {
  studioClient,
  type KnowledgeMemberRole,
  type StudioDirectoryUser,
  type StudioKnowledgeBaseMember,
} from "../../lib/studio-client";
import styles from "./knowledge-members-panel.module.css";

const ROLE_LABELS: Record<KnowledgeMemberRole, string> = {
  viewer: "查看",
  editor: "编辑",
};

export function KnowledgeMembersPanel({ reference }: { reference: string }) {
  const [members, setMembers] = useState<StudioKnowledgeBaseMember[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState<StudioDirectoryUser[]>([]);
  const [selected, setSelected] = useState<StudioDirectoryUser[]>([]);
  const [emails, setEmails] = useState("");
  const [role, setRole] = useState<KnowledgeMemberRole>("viewer");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setMembers(await studioClient.listKnowledgeMembers(reference));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载成员失败");
    } finally {
      setLoading(false);
    }
  }, [reference]);

  useEffect(() => {
    void load();
  }, [load]);

  const search = useCallback(async () => {
    const value = query.trim();
    if (!value) {
      setCandidates([]);
      return;
    }
    setError("");
    try {
      setCandidates(await studioClient.searchDirectoryUsers(value));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "搜索用户失败");
    }
  }, [query]);

  const toggleCandidate = useCallback((user: StudioDirectoryUser) => {
    setSelected((current) =>
      current.some((item) => item.userId === user.userId)
        ? current.filter((item) => item.userId !== user.userId)
        : [...current, user],
    );
  }, []);

  const submit = useCallback(async () => {
    const emailList = emails
      .split(/[,，\s]+/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (selected.length === 0 && emailList.length === 0) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await studioClient.addKnowledgeMembers(reference, {
        userIds: selected.map((item) => item.userId),
        emails: emailList,
        role,
      });
      const parts = [`已添加 ${result.members.length} 位成员（${ROLE_LABELS[role]}）`];
      if (result.unresolved.length > 0) {
        parts.push(`未找到：${result.unresolved.join("、")}`);
      }
      setNotice(parts.join("；"));
      setSelected([]);
      setEmails("");
      setCandidates([]);
      setQuery("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "添加成员失败");
    } finally {
      setBusy(false);
    }
  }, [emails, load, reference, role, selected]);

  const changeRole = useCallback(
    async (member: StudioKnowledgeBaseMember, next: KnowledgeMemberRole) => {
      setError("");
      try {
        await studioClient.updateKnowledgeMemberRole(reference, member.memberId, next);
        await load();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "修改角色失败");
      }
    },
    [load, reference],
  );

  const remove = useCallback(
    async (member: StudioKnowledgeBaseMember) => {
      const label = member.displayName || member.email || member.subjectId;
      if (!window.confirm(`移除成员「${label}」？`)) return;
      setError("");
      try {
        await studioClient.removeKnowledgeMember(reference, member.memberId);
        setNotice("成员已移除");
        await load();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "移除成员失败");
      }
    },
    [load, reference],
  );

  return (
    <div className={styles.layout}>
      <section className={styles.panel}>
        <header className={styles.panelHead}>
          <h3>添加成员</h3>
          <span className={styles.hint}>按用户搜索或粘贴邮箱批量添加</span>
        </header>

        <div className={styles.searchRow}>
          <input
            className={styles.input}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void search();
            }}
            placeholder="搜索姓名或邮箱…"
          />
          <button type="button" className={styles.ghost} onClick={() => void search()}>
            搜索
          </button>
        </div>

        {candidates.length > 0 ? (
          <ul className={styles.candidateList}>
            {candidates.map((user) => {
              const active = selected.some((item) => item.userId === user.userId);
              return (
                <li key={user.userId}>
                  <button
                    type="button"
                    className={`${styles.candidate} ${active ? styles.candidateActive : ""}`}
                    onClick={() => toggleCandidate(user)}
                  >
                    <span className={styles.candidateName}>
                      {user.displayName || user.email}
                    </span>
                    <span className={styles.candidateEmail}>{user.email}</span>
                    <span className={styles.check}>{active ? "✓" : "+"}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}

        <label className={styles.field}>
          <span>批量邮箱（逗号或空格分隔）</span>
          <textarea
            value={emails}
            onChange={(event) => setEmails(event.target.value)}
            placeholder="alice@shdata.com, bob@shdata.com"
          />
        </label>

        <div className={styles.roleRow}>
          <span className={styles.fieldLabel}>权限</span>
          {(Object.keys(ROLE_LABELS) as KnowledgeMemberRole[]).map((item) => (
            <button
              key={item}
              type="button"
              className={`${styles.roleChip} ${role === item ? styles.roleChipActive : ""}`}
              onClick={() => setRole(item)}
            >
              {ROLE_LABELS[item]}
            </button>
          ))}
          <button
            type="button"
            className={styles.primary}
            disabled={busy || (selected.length === 0 && !emails.trim())}
            onClick={() => void submit()}
          >
            {busy ? "添加中…" : `添加${selected.length > 0 ? ` (${selected.length})` : ""}`}
          </button>
        </div>

        <p className={styles.orgNote}>
          组织机构树批量授权（IDAAS）为二期功能；本期按用户逐个授权，数据模型已预留。
        </p>
      </section>

      <section className={styles.panel}>
        <header className={styles.panelHead}>
          <h3>成员（{members.length}）</h3>
          <span className={styles.hint}>查看可读与检索，编辑可上传/删除文档</span>
        </header>

        {error ? <p className={styles.error}>{error}</p> : null}
        {notice ? <p className={styles.notice}>{notice}</p> : null}

        {loading ? (
          <p className={styles.empty}>加载中…</p>
        ) : members.length === 0 ? (
          <p className={styles.empty}>还没有成员。创建者始终拥有完整权限。</p>
        ) : (
          <ul className={styles.memberList}>
            {members.map((member) => (
              <li key={member.memberId} className={styles.memberRow}>
                <div className={styles.memberInfo}>
                  <span className={styles.memberName}>
                    {member.displayName || member.email || member.subjectId}
                  </span>
                  <span className={styles.memberMeta}>
                    {member.email || member.subjectId} ·{" "}
                    {member.subjectType === "user" ? "直授" : "组织继承"}
                  </span>
                </div>
                <select
                  className={styles.select}
                  value={member.role}
                  onChange={(event) =>
                    void changeRole(member, event.target.value as KnowledgeMemberRole)
                  }
                >
                  {(Object.keys(ROLE_LABELS) as KnowledgeMemberRole[]).map((item) => (
                    <option key={item} value={item}>
                      {ROLE_LABELS[item]}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => void remove(member)}
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
