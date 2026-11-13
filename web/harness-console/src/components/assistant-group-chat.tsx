"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addAssistantGroupMember,
  assistantRequest,
  bindAssistantGroupKnowledgeBase,
  deleteAssistantGroup,
  deleteAssistantGroupDocument,
  deleteAssistantGroupMessage,
  getAssistantGroupLibrary,
  listAssistantGroupMessages,
  listAssistantGroups,
  markAssistantGroupRead,
  postAssistantGroupMessage,
  removeAssistantGroupMember,
  setAssistantGroupReplyAs,
  toggleAssistantGroupReaction,
  unbindAssistantGroupKnowledgeBase,
  updateAssistantGroup,
  uploadAssistantGroupDocument,
  type AssistantGroup,
  type AssistantGroupLibrary,
  type GroupLibraryDocument,
  type GroupMember,
  type GroupMessage,
  type Specialist,
} from "../lib/assistant-client";
import { studioClient, type StudioKnowledgeBase } from "../lib/studio-client";
import { loadGroupComposerDraft, persistGroupComposerDraft } from "../lib/group-composer-draft";
import { botAccent } from "./bot-rail";
import { AssistantGroupComposer } from "./assistant-group-composer";
import { AssistantGroupInfoPanel, type GroupProfileUpdate } from "./assistant-group-info-panel";
import { AssistantGroupMessage } from "./assistant-group-message";
import { AssistantGroupSidePanel, type GroupSideTab } from "./assistant-group-side-panel";
import { groupStampLabel } from "../lib/group-chat-time";
import styles from "./assistant-group-chat.module.css";

const PRIMARY_MEMBER_ID = "primary";
/** 相邻两条消息超过这段时间就插一个时间分隔。 */
const STAMP_GAP_MS = 10 * 60 * 1000;
/** 引用条里展示的文本长度；服务端快照本身已截断到 200 字。 */
const QUOTE_PREVIEW_CHARS = 120;

function newClientId(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

type AssistantGroupChatProps = {
  groupId: string;
  /** 当前登录用户：创建者才能改名/解散，草稿按用户隔离。 */
  userId?: string;
  /** 群名回传给主界面页头；群被删或未加载时传 null。 */
  onGroupName?: (name: string | null) => void;
  /** 成员头像簇回传给页头；群被删或未加载时传空数组。 */
  onGroupMembers?: (members: GroupMember[]) => void;
  /** 群解散后让主界面退出这个群。 */
  onDissolved?: () => void;
};

/**
 * 主界面聊天区里的群会话：花名册、接话人、消息、@、未读、引用和表情都在这里，
 * 不再自带左栏或独立顶栏——群列表由主界面左侧 BotRail 负责。
 */
export function AssistantGroupChat({
  groupId,
  userId = "",
  onGroupName,
  onGroupMembers,
  onDissolved,
}: AssistantGroupChatProps) {
  const [groups, setGroups] = useState<AssistantGroup[]>([]);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [messages, setMessages] = useState<GroupMessage[]>([]);
  /** 首轮消息还没回来时不要先闪一句「还没有消息」。 */
  const [messagesReady, setMessagesReady] = useState(false);
  // 群切换时组件按 groupId 重挂载，草稿可直接从本地恢复。
  const [draft, setDraft] = useState(() =>
    // 服务端预渲染没有 window；草稿只在浏览器里恢复。
    userId && typeof window !== "undefined"
      ? loadGroupComposerDraft(window.localStorage, userId, groupId)
      : "",
  );
  /** 当前正在引用的消息；发送成功后才清空。 */
  const [quoted, setQuoted] = useState<GroupMessage | null>(null);
  /** 将随下一条消息发送的群资料库文档。 */
  const [attachmentIds, setAttachmentIds] = useState<string[]>([]);
  const [library, setLibrary] = useState<AssistantGroupLibrary | null>(null);
  const [libraryLoading, setLibraryLoading] = useState(false);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const [libraryBusy, setLibraryBusy] = useState(false);
  const [availableKnowledgeBases, setAvailableKnowledgeBases] = useState<StudioKnowledgeBase[]>([]);
  const [sideOpen, setSideOpen] = useState(false);
  const [sideTab, setSideTab] = useState<GroupSideTab>("activity");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
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
  const isCreator = Boolean(userId && selected?.creatorUserId === userId);

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

  // 草稿按群保留：切走再回来、刷新页面都还在。
  useEffect(() => {
    if (!userId) return;
    persistGroupComposerDraft(window.localStorage, userId, groupId, draft);
  }, [draft, groupId, userId]);

  const refreshLibrary = useCallback(async (quiet = false) => {
    if (!quiet) setLibraryLoading(true);
    try {
      setLibrary(await getAssistantGroupLibrary(groupId));
      setLibraryError(null);
    } catch (reason) {
      // 静默失败不打扰对话，但保留原因：侧栏打开时显示，composer 显示「暂时不可用」。
      const message = reason instanceof Error ? reason.message : "";
      setLibraryError(
        message.includes("（404）")
          ? "当前服务端还没有群资料库，部署后可用"
          : message || "资料库读取失败",
      );
    } finally {
      if (!quiet) setLibraryLoading(false);
    }
  }, [groupId]);

  // 进群先静默拉一次资料库，供 composer 的「+」列出文件；失败不打扰对话。
  useEffect(() => {
    void refreshLibrary(true);
  }, [refreshLibrary]);

  // 打开资料库 tab 时补拉一次，并载入可绑定的知识库。
  useEffect(() => {
    if (!sideOpen || sideTab !== "library") return;
    void refreshLibrary(false);
    let cancelled = false;
    void studioClient
      .listKnowledgeBases()
      .then((list) => {
        if (!cancelled) setAvailableKnowledgeBases(list);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [sideOpen, sideTab, refreshLibrary]);

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

  const handleAdd = async (specialistId: string) => {
    if (!selected || !specialistId || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      replaceGroup(await addAssistantGroupMember(selected.groupId, specialistId));
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
        attachmentIds,
      );
      // 成功才清草稿、引用和附件；失败时都保留，避免用户重选。
      setDraft("");
      setQuoted(null);
      setAttachmentIds([]);
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

  const handleUpdateProfile = async (fields: GroupProfileUpdate): Promise<boolean> => {
    if (!selected || busy) return false;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      replaceGroup(await updateAssistantGroup(selected.groupId, fields));
      await refreshGroups();
      // 左侧栏的群名同步刷新。
      window.dispatchEvent(new CustomEvent("kai:groups-changed"));
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "群资料保存失败");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleDissolve = async (): Promise<boolean> => {
    if (!selected || busy) return false;
    setBusy(true);
    setError(null);
    try {
      await deleteAssistantGroup(selected.groupId);
      window.dispatchEvent(new CustomEvent("kai:groups-changed"));
      setSettingsOpen(false);
      setSideOpen(false);
      onGroupName?.(null);
      onDissolved?.();
      return true;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "解散群聊失败");
      return false;
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteMessage = async (message: GroupMessage) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await deleteAssistantGroupMessage(message.groupId, message.messageId);
      setMessages((current) => current.filter((item) => item.messageId !== message.messageId));
      setNotice("消息已删除");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除消息失败");
    } finally {
      setBusy(false);
    }
  };

  const openSidePanel = (tab: GroupSideTab = "activity") => {
    setSideTab(tab);
    setSideOpen(true);
    setSettingsOpen(false);
  };

  const handleUploadDocument = async (file: File) => {
    if (!selected || libraryBusy) return;
    setLibraryBusy(true);
    setLibraryError(null);
    try {
      await uploadAssistantGroupDocument(selected.groupId, file);
      await refreshLibrary(false);
    } catch (reason) {
      setLibraryError(reason instanceof Error ? reason.message : "上传失败");
    } finally {
      setLibraryBusy(false);
    }
  };

  const handleDeleteDocument = async (document: GroupLibraryDocument) => {
    if (!selected || libraryBusy) return;
    setLibraryBusy(true);
    setLibraryError(null);
    try {
      await deleteAssistantGroupDocument(selected.groupId, document.documentId);
      setAttachmentIds((current) => current.filter((id) => id !== document.documentId));
      await refreshLibrary(false);
    } catch (reason) {
      setLibraryError(reason instanceof Error ? reason.message : "删除文件失败");
    } finally {
      setLibraryBusy(false);
    }
  };

  const handleBindKnowledgeBase = async (reference: string) => {
    if (!selected || libraryBusy) return;
    setLibraryBusy(true);
    setLibraryError(null);
    try {
      await bindAssistantGroupKnowledgeBase(selected.groupId, reference);
      await refreshLibrary(false);
    } catch (reason) {
      setLibraryError(reason instanceof Error ? reason.message : "绑定知识库失败");
    } finally {
      setLibraryBusy(false);
    }
  };

  const handleUnbindKnowledgeBase = async (reference: string) => {
    if (!selected || libraryBusy) return;
    setLibraryBusy(true);
    setLibraryError(null);
    try {
      await unbindAssistantGroupKnowledgeBase(selected.groupId, reference);
      await refreshLibrary(false);
    } catch (reason) {
      setLibraryError(reason instanceof Error ? reason.message : "解绑知识库失败");
    } finally {
      setLibraryBusy(false);
    }
  };

  const toggleAttachment = (documentId: string) => {
    setAttachmentIds((current) => {
      if (current.includes(documentId)) return current.filter((id) => id !== documentId);
      if (current.length >= 10) return current;
      return [...current, documentId];
    });
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
      <div className={styles.main}>
        <div className={styles.settings} ref={settingsRef}>
          <div className={styles.settingsButtons}>
            <button
              type="button"
              className={styles.settingsButton}
              aria-label="打开群右侧栏"
              title="动态 / 任务 / 资料库"
              aria-expanded={sideOpen}
              onClick={() => openSidePanel("activity")}
            >
              <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <rect x="3" y="4" width="14" height="12" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
                <path d="M12.5 4v12" fill="none" stroke="currentColor" strokeWidth="1.5" />
              </svg>
            </button>
            <button
              type="button"
              className={styles.settingsButton}
              aria-label="群信息"
              title="群信息"
              aria-haspopup="dialog"
              aria-expanded={settingsOpen}
              onClick={() => setSettingsOpen((open) => !open)}
            >
              ⋯
            </button>
          </div>
          {settingsOpen && (
            <AssistantGroupInfoPanel
              group={selected}
              isCreator={isCreator}
              busy={busy}
              error={error}
              specialists={specialists}
              onUpdate={handleUpdateProfile}
              onAddMember={(specialistId) => void handleAdd(specialistId)}
              onRemoveMember={(memberId, name) => void handleRemove(memberId, name)}
              onReplyAs={(value) => void handleReplyAs(value)}
              onOpenLibrary={() => openSidePanel("library")}
              onDissolve={handleDissolve}
              onClose={() => setSettingsOpen(false)}
            />
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
                {error && !settingsOpen && <div className={styles.error} role="alert">{error}</div>}
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
              const canDelete =
                message.status !== "running" &&
                (message.author === "user" || isCreator);
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
                    canDelete={canDelete}
                    onDelete={(target) => void handleDeleteMessage(target)}
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
              libraryDocuments={library?.documents ?? []}
              libraryStatus={library ? "ready" : libraryError ? "unavailable" : "loading"}
              attachmentIds={attachmentIds}
              onToggleAttachment={toggleAttachment}
              onOpenLibrary={() => openSidePanel("library")}
            />
          </div>
        </footer>
      </div>
      {sideOpen && (
        <AssistantGroupSidePanel
          group={selected}
          messages={orderedMessages}
          currentUserId={userId}
          isCreator={isCreator}
          tab={sideTab}
          onTabChange={setSideTab}
          onClose={() => setSideOpen(false)}
          library={library}
          libraryLoading={libraryLoading}
          libraryError={libraryError}
          availableKnowledgeBases={availableKnowledgeBases}
          libraryBusy={libraryBusy}
          onUpload={(file) => void handleUploadDocument(file)}
          onDeleteDocument={(document) => void handleDeleteDocument(document)}
          onBind={(reference) => void handleBindKnowledgeBase(reference)}
          onUnbind={(base) => void handleUnbindKnowledgeBase(base.reference)}
        />
      )}
    </section>
  );
}
