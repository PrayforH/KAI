"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addAssistantGroupMember,
  assistantRequest,
  listAssistantGroupMessages,
  listAssistantGroups,
  markAssistantGroupRead,
  postAssistantGroupMessage,
  removeAssistantGroupMember,
  setAssistantGroupReplyAs,
  toggleAssistantGroupReaction,
  type AssistantGroup,
  type GroupMember,
  type GroupMessage,
  type Specialist,
} from "../lib/assistant-client";
import { botAccent } from "./bot-rail";
import { AssistantGroupComposer } from "./assistant-group-composer";
import { AssistantGroupMessage } from "./assistant-group-message";
import { groupStampLabel } from "../lib/group-chat-time";
import styles from "./assistant-group-chat.module.css";

const PRIMARY_MEMBER_ID = "primary";
const PERSONAL_THREAD = "kai-personal-assistant-v1";
/** 相邻两条消息超过这段时间就插一个时间分隔。 */
const STAMP_GAP_MS = 10 * 60 * 1000;
/** 引用条里展示的文本长度；服务端快照本身已截断到 200 字。 */
const QUOTE_PREVIEW_CHARS = 120;

function newClientId(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

type AssistantGroupChatProps = {
  groupId: string;
  /** 群名回传给主界面页头；群被删或未加载时传 null。 */
  onGroupName?: (name: string | null) => void;
  /** 成员头像簇回传给页头；群被删或未加载时传空数组。 */
  onGroupMembers?: (members: GroupMember[]) => void;
};

/**
 * 主界面聊天区里的群会话：花名册、接话人、消息、@、未读、引用和表情都在这里，
 * 不再自带左栏或独立顶栏——群列表由主界面左侧 BotRail 负责。
 */
export function AssistantGroupChat({
  groupId,
  onGroupName,
  onGroupMembers,
}: AssistantGroupChatProps) {
  const [groups, setGroups] = useState<AssistantGroup[]>([]);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [messages, setMessages] = useState<GroupMessage[]>([]);
  /** 首轮消息还没回来时不要先闪一句「还没有消息」。 */
  const [messagesReady, setMessagesReady] = useState(false);
  const [draft, setDraft] = useState("");
  /** 当前正在引用的消息；发送成功后才清空。 */
  const [quoted, setQuoted] = useState<GroupMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [memberToAdd, setMemberToAdd] = useState("");
  /** 进群时已有的未读条数，用来决定「新」分割线放在哪。 */
  const [unreadSeed, setUnreadSeed] = useState<number | null>(null);
  const [dividerIndex, setDividerIndex] = useState<number | null>(null);
  const [newPill, setNewPill] = useState(0);
  /** 花名册/邀请/接话人平时收起，点右上角「⋯」才展开，不占对话区。 */
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsRef = useRef<HTMLDivElement | null>(null);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const seenCounts = useRef<Record<string, number>>({});
  const dividerPlaced = useRef(false);
  const lastMessageCount = useRef(0);
  const atBottom = useRef(true);

  const selected = useMemo(
    () => groups.find((group) => group.groupId === groupId) ?? null,
    [groups, groupId],
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

  // 点面板外面或按 Esc 收起群设置。
  useEffect(() => {
    if (!settingsOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!settingsRef.current?.contains(event.target as Node)) setSettingsOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSettingsOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [settingsOpen]);

  // 页头的成员头像簇被点击时打开同一块群设置面板。
  useEffect(() => {
    const open = () => setSettingsOpen(true);
    window.addEventListener("kai:group-settings-open", open);
    return () => window.removeEventListener("kai:group-settings-open", open);
  }, []);

  const refreshGroups = useCallback(async () => {
    const list = await listAssistantGroups();
    setGroups(list);
  }, []);

  const refreshMessages = useCallback(async (target: string) => {
    setMessages(await listAssistantGroupMessages(target));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const [groupList, specialistList] = await Promise.all([
          listAssistantGroups(),
          assistantRequest<Specialist[]>("specialists").catch(() => [] as Specialist[]),
        ]);
        if (cancelled) return;
        setGroups(groupList);
        setSpecialists(specialistList);
        setUnreadSeed(
          groupList.find((group) => group.groupId === groupId)?.unreadCount ?? 0,
        );
        setError(null);
      } catch (reason) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : "群聊加载失败");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [groupId]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const list = await listAssistantGroupMessages(groupId);
        if (cancelled) return;
        setMessages(list);
        setMessagesReady(true);
        setError(null);
        if (
          !atBottom.current &&
          lastMessageCount.current > 0 &&
          list.length > lastMessageCount.current
        ) {
          setNewPill((count) => count + (list.length - lastMessageCount.current));
        }
        lastMessageCount.current = list.length;
        const seen = seenCounts.current[groupId];
        // 首次进入或群里有新消息时清未读；轮询无变化不写库。
        if (seen === undefined || list.length > seen) {
          seenCounts.current[groupId] = list.length;
          void markAssistantGroupRead(groupId)
            .then(() => {
              setGroups((current) =>
                current.map((group) =>
                  group.groupId === groupId ? { ...group, unreadCount: 0 } : group,
                ),
              );
              // 让左侧栏立刻刷新未读徽章，不必等下一次轮询。
              window.dispatchEvent(new CustomEvent("kai:groups-changed"));
            })
            .catch(() => undefined);
        }
      } catch (reason) {
        if (!cancelled) {
          setMessagesReady(true);
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
  }, [groupId]);

  useEffect(() => {
    // 用户往上翻历史时不抢滚动位置，只在贴底时跟随新消息。
    if (!atBottom.current) return;
    const node = timelineRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);

  useEffect(() => {
    // 首次拿到消息和进群时的未读数之后，把「新」分割线固定下来，之后不再移动。
    if (dividerPlaced.current || unreadSeed === null || messages.length === 0) return;
    dividerPlaced.current = true;
    setDividerIndex(unreadSeed > 0 ? Math.max(0, messages.length - unreadSeed) : null);
  }, [unreadSeed, messages.length]);

  const scrollToBottom = () => {
    const node = timelineRef.current;
    if (node) node.scrollTop = node.scrollHeight;
    atBottom.current = true;
    setNewPill(0);
  };

  useEffect(() => {
    onGroupName?.(selected?.name ?? null);
  }, [onGroupName, selected?.name]);

  useEffect(() => {
    onGroupMembers?.(selected?.members ?? []);
  }, [onGroupMembers, selected?.members]);

  const replaceGroup = (group: AssistantGroup) => {
    setGroups((current) =>
      current.map((item) => (item.groupId === group.groupId ? group : item)),
    );
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
      const result = await postAssistantGroupMessage(
        selected.groupId,
        text,
        newClientId(),
        quoted?.messageId ?? null,
      );
      // 成功才清草稿和引用；失败时两者都保留，避免用户重打。
      setDraft("");
      setQuoted(null);
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
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const handleReaction = async (message: GroupMessage, emoji: string) => {
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

  const handleQuote = (message: GroupMessage) => {
    setQuoted(message);
    requestAnimationFrame(() => composerRef.current?.focus());
  };

  const mentionTargets = useMemo(
    () => [...(primaryMember ? [primaryMember] : []), ...activeSpecialists],
    [primaryMember, activeSpecialists],
  );

  // 回复占位与用户消息共用同一条 created_at，直接按接口顺序会把「正在回复…」
  // 排到提问之前；时间相同就把用户消息（以及系统提示）放在前面。
  const orderedMessages = useMemo(() => {
    const rank = (message: GroupMessage) =>
      message.author === "user" ? 0 : message.author === "system" ? 1 : 2;
    return [...messages].sort((a, b) => {
      const left = Date.parse(a.createdAt);
      const right = Date.parse(b.createdAt);
      if (left !== right) return left - right;
      return rank(a) - rank(b);
    });
  }, [messages]);

  if (!selected) {
    return (
      <section className={styles.chat} aria-label="群聊">
        <div className={styles.empty}>
          {loading ? "正在加载群聊…" : "这个群聊不存在或已解散。"}
        </div>
      </section>
    );
  }

  return (
    <section className={styles.chat} aria-label={`群聊 ${selected.name}`}>
      <div className={styles.settings} ref={settingsRef}>
        <button
          type="button"
          className={styles.settingsButton}
          aria-label="群设置"
          title="群设置"
          aria-expanded={settingsOpen}
          onClick={() => setSettingsOpen((open) => !open)}
        >
          ⋯
        </button>
        {settingsOpen && (
          <div className={styles.settingsPanel} role="dialog" aria-label="群设置">
            <div className={styles.roster}>
              {selected.members.map((member) => {
                const role =
                  member.kind === "creator"
                    ? "创建者"
                    : member.kind === "primary"
                      ? "主助理"
                      : "子助理";
                return (
                <span key={member.memberId} className={styles.chip}>
                  {member.name}
                  {/* 花名册里的名字本身就叫「创建者/主助理」时不再重复角色。 */}
                  {member.name !== role && <small>{role}</small>}
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
                );
              })}
            </div>
            <div className={styles.settingsRow}>
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
                  className={styles.inviteButton}
                  disabled={!memberToAdd || busy}
                  onClick={() => void handleAdd()}
                >
                  邀请
                </button>
              </span>
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
                    !activeSpecialists.some((member) => member.memberId === selected.replyAs) && (
                      <option value={selected.replyAs}>{selected.replyAs}（已移出）</option>
                    )}
                  {activeSpecialists.map((member) => (
                    <option key={member.memberId} value={member.memberId}>
                      {member.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>
        )}
      </div>
      <div
        className={styles.timeline}
        ref={timelineRef}
        onScroll={() => {
          const node = timelineRef.current;
          if (!node) return;
          atBottom.current =
            node.scrollHeight - node.scrollTop - node.clientHeight < 48;
          if (atBottom.current) setNewPill(0);
        }}
      >
        <div className={styles.thread}>
          {(notice || error) && (
            <div className={styles.bannerStack}>
              {notice && <div className={styles.notice}>{notice}</div>}
              {error && <div className={styles.error} role="alert">{error}</div>}
            </div>
          )}
          {messagesReady && orderedMessages.length === 0 && (
            <div className={styles.empty}>还没有消息，打个招呼吧。</div>
          )}
          {orderedMessages.map((message, index) => {
            const previous = orderedMessages[index - 1];
            const stamp = groupStampLabel(message.createdAt);
            const gap = previous
              ? Date.parse(message.createdAt) - Date.parse(previous.createdAt)
              : Number.POSITIVE_INFINITY;
            // 首条、隔了 10 分钟以上、跨天，都插一条时间分隔。
            const showStamp = !previous || gap > STAMP_GAP_MS || gap < 0;
            const member = selected.members.find(
              (item) => item.name === message.authorName,
            );
            const tone =
              message.author === "user"
                ? "#6b746e"
                : botAccent(member?.specialistId ?? message.authorName);
            return (
              <Fragment key={message.messageId}>
                {showStamp && <div className={styles.stamp}>{stamp}</div>}
                {dividerIndex === index && (
                  <div className={styles.unreadLine} role="separator" aria-label="新消息分隔">
                    <span>新消息</span>
                  </div>
                )}
                <AssistantGroupMessage
                  message={message}
                  tone={tone}
                  onReact={(target, emoji) => void handleReaction(target, emoji)}
                  onReply={handleQuote}
                />
              </Fragment>
            );
          })}
        </div>
      </div>
      <footer className={styles.composerArea}>
        <div className={styles.composerInner}>
          {newPill > 0 && (
            <div className={styles.newPill}>
              <button type="button" className={styles.newPillJump} onClick={scrollToBottom}>
                ↓ {newPill} 条新消息
              </button>
              <button
                type="button"
                className={styles.newPillClose}
                aria-label="收起新消息提示"
                onClick={() => setNewPill(0)}
              >
                ×
              </button>
            </div>
          )}
          <AssistantGroupComposer
            value={draft}
            onChange={setDraft}
            onSend={() => void handleSend()}
            onMention={insertMention}
            sending={busy}
            placeholder={
              selected.primaryPresent
                ? `给 ${selected.name} 发消息`
                : "主助理已移出：未 @ 的消息不会改派，请 @ 子助理"
            }
            members={mentionTargets.map((member) => ({
              memberId: member.memberId,
              name: member.name,
            }))}
            quoted={
              quoted
                ? {
                    authorName: quoted.authorName,
                    text: quoted.text.slice(0, QUOTE_PREVIEW_CHARS),
                  }
                : null
            }
            onClearQuote={() => setQuoted(null)}
            textareaRef={composerRef}
            hint="Enter 发送 · Shift+Enter 换行 · 消息与回复只保存在本群"
          />
        </div>
      </footer>
    </section>
  );
}
