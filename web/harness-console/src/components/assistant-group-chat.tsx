"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  addAssistantGroupMember,
  assistantRequest,
  bindAssistantGroupKnowledgeBase,
  deleteAssistantGroupDocument,
  getAssistantGroupLibrary,
  listAssistantGroupMessages,
  listAssistantGroups,
  markAssistantGroupRead,
  postAssistantGroupMessage,
  removeAssistantGroupMember,
  toggleAssistantGroupReaction,
  unbindAssistantGroupKnowledgeBase,
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
import { AssistantGroupMembersDialog, type MemberChange } from "./assistant-group-members-dialog";
import { AssistantGroupMessage } from "./assistant-group-message";
import { AssistantGroupSidePanel, type GroupSideTab } from "./assistant-group-side-panel";
import { groupStampLabel } from "../lib/group-chat-time";
import styles from "./assistant-group-chat.module.css";

/** 相邻两条消息超过这段时间就插一个时间分隔。 */
const STAMP_GAP_MS = 10 * 60 * 1000;
/** 引用条里展示的文本长度；服务端快照本身已截断到 200 字。 */
const QUOTE_PREVIEW_CHARS = 120;
/** 消息轮询间隔；同一轮里顺带确认群元数据，助理在服务端改名/解散后能跟上。 */
const GROUP_POLL_MS = 2500;

function newClientId(): string {
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** assistantRequest 对 404 的固定文案；只有它算「群可能不存在」，5xx/网络错误不算。 */
function isNotFoundError(reason: unknown): boolean {
  return reason instanceof Error && /（404）|\(404\)/.test(reason.message);
}

/** 群元数据指纹：决定标题、成员簇和左侧栏要不要跟着刷新，不含未读数。 */
function groupMetadataKey(list: AssistantGroup[]): string {
  return JSON.stringify(
    list.map((group) => [
      group.groupId,
      group.name,
      group.description ?? null,
      group.creatorUserId,
      group.primaryPresent,
      group.replyAs,
      group.members.map((member) => [
        member.memberId,
        member.kind,
        member.name,
        member.status,
      ]),
    ]),
  );
}

type AssistantGroupChatProps = {
  groupId: string;
  /** 当前登录用户：草稿按用户隔离，也是资料库文件删除权限的判定依据。 */
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
  const [sideTab, setSideTab] = useState<GroupSideTab>("history");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 已确认群不存在：停止轮询、移除输入框，并通知主界面离场。 */
  const [dissolved, setDissolved] = useState(false);
  /** 群列表暂时读不到时，区分「未知」与「不存在」，不误导成已解散。 */
  const [metadataError, setMetadataError] = useState<string | null>(null);
  /** 进群时已有的未读条数，用来决定「新」分割线放在哪。 */
  const [unreadSeed, setUnreadSeed] = useState<number | null>(null);
  const [dividerIndex, setDividerIndex] = useState<number | null>(null);
  const [newPill, setNewPill] = useState(0);
  /** 标题带头像簇直接拉起成员管理弹窗；群管理只有这一处入口。 */
  const [membersOpen, setMembersOpen] = useState(false);
  const timelineRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const seenCounts = useRef<Record<string, number>>({});
  const dividerPlaced = useRef(false);
  const lastMessageCount = useRef(0);
  const atBottom = useRef(true);
  /** 解散只处理一次：轮询与左侧栏解散可能同时发现群已不存在。 */
  const dissolvedRef = useRef(false);
  /** 组件是否仍挂载：卸载后在途的解散响应不得再导航或改父界面状态。 */
  const mountedRef = useRef(true);
  /** 本实例当前仍有效的群；切群后旧群的在途响应必须作废。 */
  const groupIdRef = useRef(groupId);
  groupIdRef.current = groupId;
  /** 最近一次群元数据指纹，用来决定要不要刷新页面和通知左栏。 */
  const groupsKeyRef = useRef("");
  const unreadSeeded = useRef(false);
  /** 轮询里读最新的回调，避免页面每次渲染都重启定时器。 */
  const callbacksRef = useRef({ onGroupName, onGroupMembers, onDissolved });

  useEffect(() => {
    callbacksRef.current = { onGroupName, onGroupMembers, onDissolved };
  }, [onGroupName, onGroupMembers, onDissolved]);

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

  /** 吸收服务端的群列表；元数据有变化才重绘并通知左侧栏，避免每轮轮询抖动。 */
  const applyGroupList = useCallback((list: AssistantGroup[]) => {
    const key = groupMetadataKey(list);
    const previous = groupsKeyRef.current;
    if (previous === key) return;
    groupsKeyRef.current = key;
    setGroups(list);
    if (previous) window.dispatchEvent(new CustomEvent("kai:groups-changed"));
  }, []);

  /** 服务端确认群已不存在：停轮询、清活动群与输入框，再交给主界面离场。
   *  解散入口在左侧群列表的群菜单里，这里只负责下一轮轮询确认后离场；
   *  旧群或已卸载的晚到响应不得改写新群，也不得再次导航。 */
  const markDissolved = useCallback((targetGroupId?: string) => {
    if (!mountedRef.current) return;
    if (targetGroupId && targetGroupId !== groupIdRef.current) return;
    if (dissolvedRef.current) return;
    dissolvedRef.current = true;
    setDissolved(true);
    setGroups([]);
    setMessages([]);
    setMessagesReady(false);
    setError(null);
    setMetadataError(null);
    setNotice(null);
    setQuoted(null);
    setBusy(false);
    setSideOpen(false);
    window.dispatchEvent(new CustomEvent("kai:groups-changed"));
    const callbacks = callbacksRef.current;
    callbacks.onGroupName?.(null);
    callbacks.onGroupMembers?.([]);
    callbacks.onDissolved?.();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
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
    applyGroupList(await listAssistantGroups());
  }, [applyGroupList]);

  const refreshMessages = useCallback(async (target: string) => {
    setMessages(await listAssistantGroupMessages(target));
  }, []);

  // 子助理目录进群加载一次；群列表交给下面的轮询统一取，进群不重复请求。
  useEffect(() => {
    let cancelled = false;
    void assistantRequest<Specialist[]>("specialists")
      .catch(() => [] as Specialist[])
      .then((specialistList) => {
        if (!cancelled) setSpecialists(specialistList);
      });
    return () => {
      cancelled = true;
    };
  }, [groupId]);

  // 换群时先清掉上一个群的残留，避免旧消息/旧标题短暂盖住新群。
  useEffect(() => {
    setLoading(true);
    setMessages([]);
    setMessagesReady(false);
    setError(null);
    setMetadataError(null);
    lastMessageCount.current = 0;
    unreadSeeded.current = false;
    atBottom.current = true;
  }, [groupId]);

  useEffect(() => {
    if (dissolved) return undefined;
    let cancelled = false;
    // 单次加载未返回前不叠加下一轮；切群/卸载后旧请求的结果一律丢弃。
    let inFlight = false;
    // 消息 404 时先暂停消息请求，等同一轮的群列表确认，避免每 2.5 秒重复报错。
    let missingMessages = false;
    let first = true;

    const applyMessages = (list: GroupMessage[]) => {
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
    };

    const poll = async () => {
      if (inFlight || dissolvedRef.current) return;
      inFlight = true;
      try {
        const [groupsResult, messagesResult] = await Promise.allSettled([
          listAssistantGroups(),
          missingMessages
            ? Promise.resolve<GroupMessage[] | null>(null)
            : listAssistantGroupMessages(groupId),
        ]);
        if (cancelled) return;

        if (groupsResult.status === "fulfilled") {
          const groupList = groupsResult.value;
          if (!groupList.some((group) => group.groupId === groupId)) {
            // 群列表是权威来源：只有确认不存在才离场；网络/5xx 不走到这里。
            markDissolved(groupId);
            return;
          }
          applyGroupList(groupList);
          setMetadataError(null);
          missingMessages = false;
          if (!unreadSeeded.current) {
            unreadSeeded.current = true;
            setUnreadSeed(
              groupList.find((group) => group.groupId === groupId)?.unreadCount ?? 0,
            );
          }
        } else {
          // 读不到群列表只是「未知」：保留当前页面继续重试，不说成已解散。
          setMetadataError("群聊信息暂时无法加载，正在重试…");
        }

        if (messagesResult.status === "fulfilled") {
          if (messagesResult.value) applyMessages(messagesResult.value);
        } else {
          setMessagesReady(true);
          if (isNotFoundError(messagesResult.reason)) {
            missingMessages = true;
            // 群列表这轮也没拿到时给一次提示；不在每个 tick 重复 404。
            if (groupsResult.status !== "fulfilled") {
              setError((current) => current ?? "消息暂时无法加载，正在重新确认群聊状态…");
            }
          } else {
            setError(
              messagesResult.reason instanceof Error
                ? messagesResult.reason.message
                : "消息加载失败",
            );
          }
        }
      } catch (reason) {
        // allSettled 不会 reject；兜底也绝不在未知错误时判定解散。
        if (!cancelled) {
          setMessagesReady(true);
          setError(reason instanceof Error ? reason.message : "消息加载失败");
        }
      } finally {
        inFlight = false;
        if (first && !cancelled) {
          first = false;
          setLoading(false);
        }
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), GROUP_POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [groupId, dissolved, applyGroupList, markDissolved]);

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

  /** 弹窗一次性保存：按差集逐个调已有的加/减接口，失败就停并返回原因。 */
  const applyMembers = async (change: MemberChange): Promise<string | null> => {
    if (!selected) return "群聊不存在";
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      let group = selected;
      for (const specialistId of change.add) {
        group = await addAssistantGroupMember(group.groupId, specialistId);
      }
      for (const member of change.remove) {
        group = await removeAssistantGroupMember(group.groupId, member.memberId);
      }
      replaceGroup(group);
      await refreshGroups();
      await refreshMessages(group.groupId);
      return null;
    } catch (reason) {
      // 可能已经改了一部分：重新拉一次花名册，别让界面和服务端继续不一致。
      void refreshGroups();
      return reason instanceof Error ? reason.message : "保存成员失败";
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

  const openSidePanel = (tab: GroupSideTab = "history") => {
    setSideTab(tab);
    setSideOpen(true);
  };

  /** 上传到群资料库；返回新文档，输入框「+」据此把它选进本条消息。 */
  const handleUploadDocument = async (file: File): Promise<GroupLibraryDocument | null> => {
    if (!selected || libraryBusy) return null;
    setLibraryBusy(true);
    setLibraryError(null);
    try {
      const document = await uploadAssistantGroupDocument(selected.groupId, file);
      await refreshLibrary(false);
      return document;
    } catch (reason) {
      setLibraryError(reason instanceof Error ? reason.message : "上传失败");
      return null;
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

  if (dissolved || !selected) {
    return (
      <section className={styles.chat} aria-label="群聊">
        <div className={styles.main}>
          <div className="assistant-surface-title" data-testid="assistant-surface-title">
            <span className="task-context-title">群聊</span>
          </div>
          <div className={styles.empty}>
            {loading && !dissolved
              ? "正在加载群聊…"
              : dissolved
                ? "这个群聊已解散。"
                : metadataError
                  ? metadataError
                  : "这个群聊不存在或已解散。"}
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className={styles.chat} aria-label={`群聊 ${selected.name}`}>
      <div className={styles.main}>
        {/* 固定标题带：群名在左，成员头像簇与右侧栏入口在右。它在消息滚动区之外，
            所以按钮不会随滚动压到气泡上（本轮返工的核心）。 */}
        <div className="assistant-surface-title" data-testid="assistant-surface-title">
          <span className="task-context-title">{selected.name}</span>
          <div className={styles.settings}>
            {/* 成员头像簇：最多 3 个，其余折成 +N；点它直接打开成员管理弹窗。
                助理面无顶栏，所以入口放在群聊自己的固定标题带里。 */}
            <button
              type="button"
              className="group-member-faces"
              title="成员管理"
              aria-label={`群成员 ${selected.members.length} 人，打开成员管理`}
              aria-haspopup="dialog"
              onClick={() => setMembersOpen(true)}
            >
              {selected.members.slice(0, 3).map((member) => (
                <span
                  key={member.memberId}
                  className="group-member-face"
                  style={{
                    background: member.kind === "creator"
                      ? "#6b746e"
                      : botAccent(member.specialistId ?? member.name),
                  }}
                >
                  {member.name.slice(0, 1)}
                </span>
              ))}
              {selected.members.length > 3 && (
                <span className="group-member-more">+{selected.members.length - 3}</span>
              )}
            </button>
            <div className={styles.settingsButtons}>
              <button
                type="button"
                className={styles.settingsButton}
                aria-label={sideOpen ? "关闭群右侧栏" : "打开群右侧栏"}
                title="聊天记录 / 动态 / 任务 / 资料库"
                aria-expanded={sideOpen}
                onClick={() => (sideOpen ? setSideOpen(false) : openSidePanel("history"))}
              >
                <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                  <rect x="3" y="4" width="14" height="12" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
                  <path d="M12.5 4v12" fill="none" stroke="currentColor" strokeWidth="1.5" />
                </svg>
              </button>
            </div>
          </div>
        </div>
        <div
          className={styles.timeline}
          data-testid="group-message-timeline"
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
              sending={busy}
              placeholder={
                selected.primaryPresent
                  ? `给 ${selected.name} 发消息`
                  : "主助理已移出：未 @ 的消息不会改派，请 @ 子助理"
              }
              members={mentionTargets.map((member) => ({
                memberId: member.memberId,
                name: member.name,
                role: member.kind,
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
              libraryDocuments={library?.documents ?? []}
              libraryStatus={library ? "ready" : libraryError ? "unavailable" : "loading"}
              attachmentIds={attachmentIds}
              onToggleAttachment={toggleAttachment}
              onUploadDocument={handleUploadDocument}
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
      {membersOpen && (
        <AssistantGroupMembersDialog
          group={selected}
          specialists={specialists}
          onApply={applyMembers}
          onClose={() => setMembersOpen(false)}
        />
      )}
    </section>
  );
}
