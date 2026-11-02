"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { CreateBotDialog } from "./create-bot-dialog";
import { BOT_SHAPES, botAppearanceTokens, botAppearanceVars } from "../lib/bot-appearance";
import { BotGlyph } from "./bot-glyph";
import { SidebarIcon } from "./sidebar-icon";
import { PERSONAL_ASSISTANT_THREAD } from "./personal-assistant-context";
import { BotRailMenu } from "./bot-rail-menu";
import { BotEditDialog } from "./bot-edit-dialog";
import { botSidebar, type BotSidebar } from "../lib/bot-sidebar";
import { stableBotOrder, isPrimaryBot, botConversationHref } from "../lib/bot-sidebar";
import { peekClientRead, rememberClientRead } from "../lib/client-read-cache";
import { handoverStore } from "../lib/handover-store";

// 取自客户端调色板（sand-fill-*-bubble-user）：每个 bot 一个稳定强调色，
// 头像、选中态与该 bot 会话里的用户气泡共用同一色。
export const BOT_ACCENTS = [
  "#126fd4", // blue（主助理默认）
  "#7442d1", // purple
  "#c82779", // magenta
  "#0d9655", // green
  "#d2550a", // orange
  "#c82532", // red
  "#0d9381", // cyan
  "#86592e", // brown
];

/** 新建时选的颜色优先，老 bot 仍按 id 取稳定强调色。 */
export function specialistAppearance(item: Specialist): { color: string; avatar: string } {
  const raw = (item.config as { appearance?: { color?: string; avatar?: string } } | undefined)?.appearance;
  return { color: raw?.color ?? botAccent(item.specialistId), avatar: raw?.avatar ?? "blob" };
}

/** 无人查看导致的例行程序暂停（其它 reason 将来再扩展，本切片只认 unviewed）。 */
export function isUnviewedPaused(item: Pick<Specialist, "routinePausedReason">): boolean {
  return item.routinePausedReason?.reason === "unviewed";
}

export function botAccent(key: string): string {
  let hash = 0;
  for (const char of key) hash = (hash * 31 + char.charCodeAt(0)) % 997;
  return BOT_ACCENTS[hash % BOT_ACCENTS.length]!;
}

function toneOf(key: string): string {
  return botAccent(key);
}

/** 已选外形；缺失或未知 id 一律回到新建默认的 blob（不回退成字母）。 */
export function botShape(avatar: string | undefined | null) {
  return BOT_SHAPES.find((shape) => shape.id === avatar) ?? BOT_SHAPES[0];
}

/**
 * 侧栏头像：子助理用「色 + 形」的身份（与新建/编辑里选的外观一致），
 * 主助理保留固定的「K」字母入口，不会被某个子助理的外形污染。
 */
export function BotRailAvatar({
  tone,
  shape,
  letter,
}: {
  tone: string;
  shape?: string | null;
  letter?: string;
}) {
  if (letter) {
    return (
      <span className="bot-rail-avatar" data-avatar="letter" style={{ background: tone }} aria-hidden="true">
        {letter}
      </span>
    );
  }
  const glyph = botShape(shape);
  // 浅色本体（白/浅灰）坐在中性灰底板上，眼睛用深墨；深色本体保持透明底与白眼睛。
  const tokens = botAppearanceTokens(tone);
  return (
    <span
      className="bot-rail-avatar"
      data-avatar="shape"
      data-shape={glyph.id}
      style={{ background: tokens.surface, ...botAppearanceVars(tone) } as React.CSSProperties}
      aria-hidden="true"
    >
      <BotGlyph shape={glyph.id} color={tokens.accent} />
    </span>
  );
}

/**
 * 左侧「机器人」区：按本人置顶/分组与稳定创建顺序组织。
 * 点击进入该助理的会话或职责面板——对话优先，配置其次。
 */
type BotRailProps = {
  currentThreadId: string; activeBotId?: string | null; userId?: string; tenantId?: string;
  groupsState?: readonly [ReadonlySet<string>, (change: (current: ReadonlySet<string>) => ReadonlySet<string>) => void];
};
export function BotRail(props: BotRailProps) {
  return <BotDirectory key={`${props.tenantId ?? ""}:${props.userId ?? ""}`} {...props} />;
}
function BotDirectory({currentThreadId, activeBotId = null, userId, tenantId, groupsState}: BotRailProps) {
  const mounted = useRef(true);
  const requestVersion = useRef(0);
  useEffect(() => { mounted.current = true; return () => {mounted.current = false;}; }, []);
  const router = useRouter();
  const activeBot = activeBotId;
  const cacheKey = userId ? `bot-directory:${tenantId ?? ""}:${userId}` : null;
  const [specialists, setSpecialists] = useState<Specialist[]>(() => cacheKey ? peekClientRead<Specialist[]>(cacheKey) ?? [] : []);
  const [localGroups, setLocalGroups] = useState<ReadonlySet<string>>(new Set());
  const [groupsView, setGroupsView] = groupsState ?? [localGroups, setLocalGroups];
  const collapsed = groupsView.has("section:bots:collapsed");
  const sectionId = useId();
  const expanded = groupsView.has("bots:all");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  const [railError, setRailError] = useState("");

  const load = useCallback(async () => {
    const version = ++requestVersion.current;
    let initialized = false;
    try {
      let list = await assistantRequest<Specialist[]>("specialists");
      if (!mounted.current || version !== requestVersion.current) return;
      if (userId && !list.some(isPrimaryBot)) {
        try {
          const primary = await assistantRequest<Specialist>("primary", "POST");
          if (!mounted.current || version !== requestVersion.current) return;
          if (primary?.specialistId && isPrimaryBot(primary)) {
            list = [...list, primary];
            rememberClientRead(`primary-bot:${tenantId ?? ""}:${userId}`, primary);
            initialized = true;
          }
        } catch { /* Existing main conversation remains available during setup failure. */ }
      }
      if (!mounted.current || version !== requestVersion.current) return;
      handoverStore.updateSpecialists(list);
      const ordered = stableBotOrder(list);
      setSpecialists(ordered);
      if (cacheKey) rememberClientRead(cacheKey, ordered);
      if (initialized) window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
    } catch {
      // 机器人列表是导航增强；读不到时保留主助理入口即可。
    }
  }, [cacheKey, userId, tenantId]);

  useEffect(() => {
    void load();
    // 打开某 Bot 会话后 viewed 会清暂停标记：页面派发该事件，这里立刻重拉，
    // 徽章不必等下一次 30s 轮询。
    const onChanged = () => void load();
    window.addEventListener("kai:specialists-changed", onChanged);
    const timer = setInterval(() => {
      if (!document.hidden) void load();
    }, 30000);
    return () => {
      window.removeEventListener("kai:specialists-changed", onChanged);
      clearInterval(timer);
    };
  }, [load]);

  async function saveSidebar(item: Specialist, patch: Partial<BotSidebar>) {
    const latest = await assistantRequest<{specialist: Specialist}>(`specialists/${encodeURIComponent(item.specialistId)}`);
    const updated = await assistantRequest<Specialist>(`specialists/${encodeURIComponent(item.specialistId)}/profile`, "POST", {
      sidebar: {...botSidebar(latest.specialist), ...patch}, expectedRevision: latest.specialist.revision,
    });
    setSpecialists(items => items.map(bot => bot.specialistId === updated.specialistId ? updated : bot));
    window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
  }
  async function changeArchive(item: Specialist, status: "archived" | "paused") {
    setRailError("");
    const latest = await assistantRequest<{specialist: Specialist}>(`specialists/${encodeURIComponent(item.specialistId)}`);
    await assistantRequest(`specialists/${encodeURIComponent(item.specialistId)}/status`, "POST", {status, expectedRevision: latest.specialist.revision});
    await load(); window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
  }
  const ordered = stableBotOrder(specialists);
  const primary = ordered.find(isPrimaryBot);
  const available = ordered.filter(item => item.status !== "archived" && !botSidebar(item).hidden);
  const pinned = available.filter(item => botSidebar(item).pinned);
  const unpinned = available.filter(item => !botSidebar(item).pinned);
  const groups = [...new Set(specialists.map(item => botSidebar(item).group).filter(Boolean))];
  const grouped = groups.map(name => ({name, items: unpinned.filter(item => botSidebar(item).group === name)})).filter(group => group.items.length);
  // Availability and organization are orthogonal; pinning never enables a Bot.
  const ungrouped = unpinned.filter(item => !botSidebar(item).group);
  const contacts = ungrouped;
  const hidden = specialists.filter(item => item.status !== "archived" && botSidebar(item).hidden);
  const archived = specialists.filter(item => item.status === "archived");
  const collapsedLimit = 5;
  const contactOrder = [...pinned, ...grouped.flatMap(group => group.items), ...contacts];
  const visible = new Set((expanded ? contactOrder : contactOrder.slice(0, collapsedLimit)).map(item => item.specialistId));
  const shown = (item: Specialist) => visible.has(item.specialistId);
  const visibleContacts = contacts.filter(shown);
  const isAssistant = !activeBot && currentThreadId === PERSONAL_ASSISTANT_THREAD;
  function row(item: Specialist) {
    return <li key={item.specialistId} className="bot-rail-row">
      <Link className="bot-rail-item" data-active={(isPrimaryBot(item) ? !activeBot && currentThreadId === PERSONAL_ASSISTANT_THREAD : activeBot === item.specialistId) ? "true" : undefined}
        data-paused={isUnviewedPaused(item) ? "true" : undefined} data-unread={botSidebar(item).unread ? "true" : undefined}
        title={isUnviewedPaused(item) ? "已暂停 · 无人查看" : undefined}
        href={botConversationHref(item)} scroll={false}>
        <BotRailAvatar tone={specialistAppearance(item).color} shape={specialistAppearance(item).avatar} />
        <span className="bot-rail-label"><strong>{item.name}</strong></span>
        {botSidebar(item).unread && <span className="bot-rail-unread" aria-label="未读" />}
      </Link>
      <BotRailMenu bot={item} groups={groups} onUpdate={patch => saveSidebar(item, patch)}
        onEdit={() => setEditing(item.specialistId)} onArchive={() => changeArchive(item, "archived")} />
    </li>;
  }
  return (
    <section className="bot-rail" aria-label="机器人">
      <div className="task-list-toolbar bot-rail-toolbar">
        <div className="task-list-heading">
        <button type="button" className="task-section-toggle" aria-expanded={!collapsed} aria-controls={sectionId}
          aria-label={`${collapsed ? "展开" : "收起"}机器人分组`} onClick={() => setGroupsView(current => {
            const next = new Set(current); if (collapsed) next.delete("section:bots:collapsed"); else next.add("section:bots:collapsed"); return next;
          })}><span>机器人</span><SidebarIcon name="chevronDown" size={12} className="task-group-chevron" /></button>
        </div>
        <button
          type="button"
          className="bot-rail-add"
          aria-label="新建 Bot"
          title="新建 Bot"
          onClick={() => setCreating(true)}
        >
          <SidebarIcon name="plus" />
        </button>
      </div>
      <div id={sectionId} hidden={collapsed}>
      <ul className="bot-rail-list">
        {!primary && <li>
          <Link
            className="bot-rail-item"
            data-active={isAssistant ? "true" : undefined}
            href="/assistant"
            scroll={false}
          >
            <BotRailAvatar letter="K" tone={BOT_ACCENTS[0]!} />
            <span className="bot-rail-label">
              <strong>主助理</strong>
            </span>
          </Link>
        </li>}
        {pinned.length > 0 && <li className="bot-rail-group">已置顶</li>}
        {pinned.filter(shown).map(row)}
        {grouped.filter(group => group.items.some(shown)).map(group => <li key={group.name} className="bot-rail-section">
          <div className="bot-rail-group">{group.name}</div><ul className="bot-rail-list">{group.items.filter(shown).map(row)}</ul>
        </li>)}
        {contacts.length > 0 && (pinned.length > 0 || grouped.length > 0) && <li className="bot-rail-group">未分组</li>}
        {visibleContacts.map(row)}
      </ul>
      {railError && <p role="alert">{railError}</p>}
      {editing && <BotEditDialog specialistId={editing} onClose={() => setEditing(null)} onSaved={() => {
        setEditing(null); void load(); window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
      }} />}
      {hidden.length > 0 && <>
        <button type="button" className="bot-rail-more" aria-expanded={showHidden} onClick={() => setShowHidden(value => !value)}>隐藏的 Bot（{hidden.length}）</button>
        {showHidden && <ul className="bot-rail-list">{hidden.map(item => <li className="bot-rail-recover" key={item.specialistId}>
          <span>{item.name}</span><button type="button" onClick={() => void saveSidebar(item, {hidden: false}).catch(error => setRailError(String(error.message)))}>恢复显示</button>
        </li>)}</ul>}
      </>}
      {archived.length > 0 && <>
        <button type="button" className="bot-rail-more" aria-expanded={showArchived} onClick={() => setShowArchived(value => !value)}>已归档（{archived.length}）</button>
        {showArchived && <ul className="bot-rail-list">{archived.map(item => <li className="bot-rail-recover" key={item.specialistId}>
          <span>{item.name}</span><button type="button" onClick={() => void changeArchive(item, "paused").catch(error => setRailError(String(error.message)))}>恢复显示，保持暂停</button>
        </li>)}</ul>}
      </>}
      </div>
      {creating && (
        <CreateBotDialog
          onClose={() => setCreating(false)}
          onCreated={(specialistId) => {
            setCreating(false);
            window.dispatchEvent(new CustomEvent("kai:specialists-changed"));
            router.push(`/assistant?bot=${encodeURIComponent(specialistId)}`, {scroll: false});
          }}
        />
      )}
      {!collapsed && contactOrder.length > collapsedLimit && (
        <button
          type="button"
          className="task-list-item tasks-show-more"
          aria-label={expanded ? "收起机器人" : "展开机器人"}
          aria-expanded={expanded}
          onClick={() => setGroupsView(current => {
            const next = new Set(current); if (expanded) next.delete("bots:all"); else next.add("bots:all"); return next;
          })}
        >
          <span className="task-list-title">{expanded ? "收起显示" : `展开显示（${contactOrder.length - collapsedLimit}）`}</span>
        </button>
      )}
    </section>
  );
}
