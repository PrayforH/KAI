"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SidebarIcon } from "../../../components/sidebar-icon";
import {
  addAssistantGroupMember,
  assistantRequest,
  createAssistantGroup,
  listAssistantGroupMessages,
  listAssistantGroups,
  markAssistantGroupRead,
  postAssistantGroupMessage,
  removeAssistantGroupMember,
  setAssistantGroupReplyAs,
  toggleAssistantGroupReaction,
  type AssistantGroup,
  type GroupMessage,
  type Specialist,
} from "../../../lib/assistant-client";
import styles from "./groups.module.css";

const PRIMARY_MEMBER_ID = "primary";
const PERSONAL_THREAD = "kai-personal-assistant-v1";
const REACTION_EMOJIS = ["👍", "❤️", "😂", "🎉", "👀"];

function timeLabel(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

function newClientId(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export default function AssistantGroupsPage() {
  const [groups, setGroups] = useState<AssistantGroup[]>([]);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<GroupMessage[]>([]);
  const [groupName, setGroupName] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [memberToAdd, setMemberToAdd] = useState("");
  const [pickerFor, setPickerFor] = useState<string | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const seenCounts = useRef<Record<string, number>>({});

  const selected = useMemo(
    () => groups.find((group) => group.groupId === selectedId) ?? null,
    [groups, selectedId],
  );
  const activeSpecialists = useMemo(
    () =>
      (selected?.members ?? []).filter(
        (member) => member.kind === "specialist" && member.status === "active",
      ),
    [selected],
  );
  const primaryMember = useMemo(
    () => (selected?.members ?? []).find((member) => member.kind === "primary") ?? null,
    [selected],
  );
  const addableSpecialists = useMemo(() => {
    const seated = new Set(
      (selected?.members ?? [])
        .map((member) => member.specialistId)
        .filter((value): value is string => Boolean(value)),
    );
    return specialists.filter(
      (item) =>
        item.status !== "archived" &&
        !seated.has(item.specialistId) &&
        item.config?.conversation_thread_id !== PERSONAL_THREAD,
    );
  }, [selected, specialists]);

  const refreshGroups = useCallback(async () => {
    const list = await listAssistantGroups();
    setGroups(list);
    setSelectedId((current) => {
      if (current && list.some((group) => group.groupId === current)) return current;
      return list[0]?.groupId ?? null;
    });
  }, []);

  const refreshMessages = useCallback(async (groupId: string) => {
    const list = await listAssistantGroupMessages(groupId);
    setMessages((current) => {
      // 轮询可能晚于切换：只接受当前群的结果。
      return list;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [groupList, specialistList] = await Promise.all([
          listAssistantGroups(),
          assistantRequest<Specialist[]>("specialists").catch(() => [] as Specialist[]),
        ]);
        if (cancelled) return;
        setGroups(groupList);
        setSpecialists(specialistList);
        setSelectedId(groupList[0]?.groupId ?? null);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "群聊加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    const timer = window.setInterval(() => {
      void listAssistantGroups()
        .then((list) => {
          if (!cancelled) setGroups(list);
        })
        .catch(() => undefined);
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const list = await listAssistantGroupMessages(selectedId);
        if (!cancelled) {
          setMessages(list);
          setError(null);
          const seen = seenCounts.current[selectedId];
          // 首次进入或群里有新消息时清未读；轮询无变化不写库。
          if (seen === undefined || list.length > seen) {
            seenCounts.current[selectedId] = list.length;
            void markAssistantGroupRead(selectedId)
              .then(() => {
                setGroups((current) =>
                  current.map((group) =>
                    group.groupId === selectedId
                      ? { ...group, unreadCount: 0 }
                      : group,
                  ),
                );
              })
              .catch(() => undefined);
          }
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "消息加载失败");
        }
      }
    };
    void load();
    const timer = window.setInterval(load, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [selectedId]);

  useEffect(() => {
    const node = timelineRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);

  const replaceGroup = (group: AssistantGroup) => {
    setGroups((current) =>
      current.map((item) => (item.groupId === group.groupId ? group : item)),
    );
  };

  const handleCreate = async () => {
    if (!groupName.trim() || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const created = await createAssistantGroup(groupName.trim());
      setGroupName("");
      await refreshGroups();
      setSelectedId(created.groupId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建群聊失败");
    } finally {
      setBusy(false);
    }
  };

  const handleAdd = async () => {
    if (!selected || !memberToAdd || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      replaceGroup(await addAssistantGroupMember(selected.groupId, memberToAdd));
      setMemberToAdd("");
      await refreshGroups();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "添加成员失败");
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (memberId: string, name: string) => {
    if (!selected || busy) return;
    if (!window.confirm(`把「${name}」移出本群？移出后 TA 不能再发言或被 @。`)) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      replaceGroup(await removeAssistantGroupMember(selected.groupId, memberId));
      await refreshGroups();
      await refreshMessages(selected.groupId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "移除成员失败");
    } finally {
      setBusy(false);
    }
  };

  const handleReplyAs = async (value: string) => {
    if (!selected || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const replyAs = value === PRIMARY_MEMBER_ID ? null : value;
      replaceGroup(await setAssistantGroupReplyAs(selected.groupId, replyAs));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "设置接话人失败");
    } finally {
      setBusy(false);
    }
  };

  const handleSend = async () => {
    if (!selected || !draft.trim() || busy) return;
    const text = draft.trim();
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await postAssistantGroupMessage(selected.groupId, text, newClientId());
      setDraft("");
      if (result.notices.length > 0) setNotice(result.notices.join(" "));
      await refreshMessages(selected.groupId);
      await refreshGroups();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发送失败");
    } finally {
      setBusy(false);
    }
  };

  const insertMention = (name: string) => {
    setDraft((current) => {
      const spacer = current && !current.endsWith(" ") ? " " : "";
      return `${current}${spacer}@${name} `;
    });
    composerRef.current?.focus();
  };

  const handleReaction = async (message: GroupMessage, emoji: string) => {
    setPickerFor(null);
    const next = message.reactions.includes(emoji)
      ? message.reactions.filter((item) => item !== emoji)
      : [...message.reactions, emoji];
    setMessages((current) =>
      current.map((item) =>
        item.messageId === message.messageId ? { ...item, reactions: next } : item,
      ),
    );
    try {
      const result = await toggleAssistantGroupReaction(
        message.groupId,
        message.messageId,
        emoji,
      );
      setMessages((current) =>
        current.map((item) =>
          item.messageId === message.messageId
            ? { ...item, reactions: result.reactions }
            : item,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "表情操作失败");
      void refreshMessages(message.groupId);
    }
  };

  const mentionTargets = useMemo(
    () => [
      ...(primaryMember ? [primaryMember] : []),
      ...activeSpecialists,
    ],
    [primaryMember, activeSpecialists],
  );

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link href="/assistant" className={styles.back}>
          <SidebarIcon name="chevronDown" />
          返回助理
        </Link>
        <h1>群聊</h1>
        <span className={styles.sub}>
          带花名册的共享会话 · @ 谁谁回复 · 未 @ 由接话人回复
        </span>
      </header>
      <div className={styles.body}>
        <aside className={styles.rail}>
          <div className={styles.create}>
            <input
              value={groupName}
              placeholder="新建群聊名称"
              maxLength={40}
              onChange={(event) => setGroupName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void handleCreate();
              }}
            />
            <button
              type="button"
              className={styles.primaryButton}
              disabled={!groupName.trim() || busy}
              onClick={() => void handleCreate()}
            >
              建群
            </button>
          </div>
          <div className={styles.groupList}>
            {groups.map((group) => (
              <button
                key={group.groupId}
                type="button"
                className={`${styles.groupItem} ${
                  group.groupId === selectedId ? styles.groupItemActive : ""
                }`}
                onClick={() => setSelectedId(group.groupId)}
              >
                <strong>
                  {group.name}
                  {group.unreadCount > 0 && (
                    <span className={styles.unreadBadge}>
                      {group.unreadCount > 99 ? "99+" : group.unreadCount}
                    </span>
                  )}
                </strong>
                <span>
                  {group.members.length} 位成员
                  {!group.primaryPresent ? " · 主助理已移出" : ""}
                </span>
              </button>
            ))}
            {!loading && groups.length === 0 && (
              <div className={styles.empty}>还没有群聊，先建一个。</div>
            )}
          </div>
        </aside>
        <section className={styles.main}>
          {!selected ? (
            <div className={styles.empty}>
              {loading ? "正在加载群聊…" : "选择左侧群聊，或先创建一个。"}
            </div>
          ) : (
            <>
              <div className={styles.groupHead}>
                <h2>{selected.name}</h2>
                <span className={styles.meta}>{selected.members.length} 位成员</span>
                <label className={styles.replyAs}>
                  未 @ 接话人
                  <select
                    value={selected.replyAs ?? PRIMARY_MEMBER_ID}
                    disabled={busy}
                    onChange={(event) => void handleReplyAs(event.target.value)}
                  >
                    <option value={PRIMARY_MEMBER_ID}>
                      主助理{selected.primaryPresent ? "" : "（已移出）"}
                    </option>
                    {selected.replyAs &&
                      selected.replyAs !== PRIMARY_MEMBER_ID &&
                      !activeSpecialists.some(
                        (member) => member.memberId === selected.replyAs,
                      ) && (
                        <option value={selected.replyAs}>
                          {selected.replyAs}（已移出）
                        </option>
                      )}
                    {activeSpecialists.map((member) => (
                      <option key={member.memberId} value={member.memberId}>
                        {member.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className={styles.roster}>
                {selected.members.map((member) => (
                  <span key={member.memberId} className={styles.chip}>
                    {member.name}
                    <small>
                      {member.kind === "creator"
                        ? "创建者"
                        : member.kind === "primary"
                          ? "主助理"
                          : "子助理"}
                    </small>
                    {member.kind !== "creator" && (
                      <button
                        type="button"
                        title={`移出 ${member.name}`}
                        aria-label={`移出 ${member.name}`}
                        disabled={busy}
                        onClick={() => void handleRemove(member.memberId, member.name)}
                      >
                        ×
                      </button>
                    )}
                  </span>
                ))}
                <span className={styles.addRow}>
                  <select
                    value={memberToAdd}
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
                    className={styles.primaryButton}
                    disabled={!memberToAdd || busy}
                    onClick={() => void handleAdd()}
                  >
                    邀请
                  </button>
                </span>
              </div>
              {notice && <div className={styles.notice}>{notice}</div>}
              {error && <div className={styles.error}>{error}</div>}
              <div className={styles.timeline} ref={timelineRef}>
                {messages.length === 0 && (
                  <div className={styles.empty}>还没有消息，打个招呼吧。</div>
                )}
                {messages.map((message) => {
                  const mine = message.author === "user";
                  const system = message.author === "system";
                  return (
                    <div
                      key={message.messageId}
                      className={`${styles.message} ${
                        mine ? styles.messageUser : ""
                      } ${system ? styles.messageSystem : ""}`}
                    >
                      <span className={styles.author}>
                        {message.authorName}
                        <span className={styles.badge}>
                          {message.author === "user"
                            ? "我"
                            : message.author === "primary"
                              ? "主助理"
                              : message.author === "specialist"
                                ? "子助理"
                                : "系统"}
                        </span>
                        <span>{timeLabel(message.createdAt)}</span>
                      </span>
                      <div className={`${styles.bubble} ${
                        message.status === "failed" ? styles.failed : ""
                      }`}>
                        {message.text || (
                          <span className={styles.running}>
                            {message.status === "waiting"
                              ? "等待确认后继续…"
                              : message.status === "running"
                                ? "正在回复…"
                                : "…"}
                          </span>
                        )}
                      </div>
                      {!system && (
                        <div className={styles.reactionRow}>
                          {message.reactions.map((emoji) => (
                            <button
                              key={emoji}
                              type="button"
                              className={`${styles.reactionChip} ${styles.reactionChipOn}`}
                              title="取消这个表情"
                              onClick={() => void handleReaction(message, emoji)}
                            >
                              {emoji}
                            </button>
                          ))}
                          <button
                            type="button"
                            className={styles.reactionAdd}
                            aria-label="添加表情"
                            title="添加表情"
                            onClick={() =>
                              setPickerFor((current) =>
                                current === message.messageId
                                  ? null
                                  : message.messageId,
                              )
                            }
                          >
                            ☺
                          </button>
                          {pickerFor === message.messageId && (
                            <span className={styles.reactionPicker}>
                              {REACTION_EMOJIS.map((emoji) => (
                                <button
                                  key={emoji}
                                  type="button"
                                  onClick={() => void handleReaction(message, emoji)}
                                >
                                  {emoji}
                                </button>
                              ))}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
              <div className={styles.composer}>
                {mentionTargets.length > 0 && (
                  <div className={styles.mentionBar}>
                    @
                    {mentionTargets.map((member) => (
                      <button
                        key={member.memberId}
                        type="button"
                        className={styles.mentionButton}
                        onClick={() => insertMention(member.name)}
                      >
                        {member.name}
                      </button>
                    ))}
                  </div>
                )}
                <div className={styles.inputRow}>
                  <textarea
                    ref={composerRef}
                    value={draft}
                    placeholder={
                      selected.primaryPresent
                        ? "发消息给本群；@ 某位成员则只由 TA 回复"
                        : "主助理已移出：未 @ 的消息不会改派，请 @ 子助理"
                    }
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault();
                        void handleSend();
                      }
                    }}
                  />
                  <button
                    type="button"
                    className={styles.primaryButton}
                    disabled={!draft.trim() || busy}
                    onClick={() => void handleSend()}
                  >
                    发送
                  </button>
                </div>
                <span className={styles.hint}>
                  Enter 发送 · Shift+Enter 换行 · 消息与回复只保存在本群
                </span>
              </div>
            </>
          )}
        </section>
      </div>
    </main>
  );
}
