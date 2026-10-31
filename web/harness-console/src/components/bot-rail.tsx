"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { BOT_SHAPES, CreateBotDialog } from "./create-bot-dialog";
import { SidebarIcon } from "./sidebar-icon";
import { PERSONAL_ASSISTANT_THREAD } from "./personal-assistant-context";

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
  return (
    <span
      className="bot-rail-avatar"
      data-avatar="shape"
      data-shape={glyph.id}
      style={{ background: "transparent" }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 64 64" focusable="false">
        <path d={glyph.path} fill={tone} />
      </svg>
    </span>
  );
}

/**
 * 助理面顶栏的身份标记：与 rail 同源——子助理用保存的色+形（未知/缺失回退 blob），
 * 主助理固定「K」字母，不会被子助理外形污染。
 */
export function AssistantHeaderAvatar({
  bot,
}: {
  bot: { specialistId: string; appearance?: { color?: string; avatar?: string } } | null;
}) {
  return (
    <span className="task-context-bot-avatar">
      <BotRailAvatar
        tone={bot ? (bot.appearance?.color ?? botAccent(bot.specialistId)) : BOT_ACCENTS[0]!}
        shape={bot ? bot.appearance?.avatar : undefined}
        letter={bot ? undefined : "K"}
      />
    </span>
  );
}

/**
 * 左侧「机器人」区：主助理置顶，专职子助理按最近更新排序。
 * 点击进入该助理的会话或职责面板——对话优先，配置其次。
 */
export function BotRail({ currentThreadId }: { currentThreadId: string }) {
  // 当前选中的机器人来自地址（?bot=），线程 id 不能用来判断。
  const [activeBot, setActiveBot] = useState<string | null>(null);
  useEffect(() => {
    const read = () => setActiveBot(new URLSearchParams(window.location.search).get("bot"));
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [creating, setCreating] = useState(false);

  function openBot(event: React.MouseEvent, specialistId: string | null) {
    // 同一路由只改查询参数时页面不会重挂载，旧线程会盖住新 bot：
    // 直接整页导航，和从地址栏打开走同一条路径。
    event.preventDefault();
    window.location.assign(
      specialistId ? `/assistant?bot=${encodeURIComponent(specialistId)}` : "/assistant",
    );
  }

  const load = useCallback(async () => {
    try {
      const list = await assistantRequest<Specialist[]>("specialists");
      setSpecialists(list.filter((item) => item.status !== "archived"));
    } catch {
      // 机器人列表是导航增强；读不到时保留主助理入口即可。
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (!document.hidden) void load();
    }, 30000);
    return () => clearInterval(timer);
  }, [load]);

  // 按真实状态分组：已启用＝在岗承担职责；其余（新建/试运行/待启用）还没接活。
  const active = specialists.filter((item) => item.status === "active");
  const pending = specialists.filter((item) => item.status !== "active");
  const collapsedLimit = 3;
  const visiblePending = expanded ? pending : pending.slice(0, collapsedLimit);
  const isAssistant = !activeBot && currentThreadId === PERSONAL_ASSISTANT_THREAD;

  return (
    <section className="bot-rail" aria-label="机器人">
      <div className="bot-rail-head">
        <span>机器人</span>
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
      <ul className="bot-rail-list">
        <li>
          <Link
            className="bot-rail-item"
            data-active={isAssistant ? "true" : undefined}
            href="/assistant"
            onClick={(event) => openBot(event, null)}
          >
            <BotRailAvatar letter="K" tone={BOT_ACCENTS[0]!} />
            <span className="bot-rail-label">
              <strong>主助理</strong>
              <small>统一接活、跟进与交付</small>
            </span>
          </Link>
        </li>
        {active.length > 0 && <li className="bot-rail-group">在岗</li>}
        {active.map((item) => (
          <li key={item.specialistId}>
            <Link
              className="bot-rail-item"
              data-active={activeBot === item.specialistId ? "true" : undefined}
              href={`/assistant?bot=${encodeURIComponent(item.specialistId)}`}
              onClick={(event) => openBot(event, item.specialistId)}
            >
              <BotRailAvatar
                tone={specialistAppearance(item).color}
                shape={specialistAppearance(item).avatar}
              />
              <span className="bot-rail-label">
                <strong>{item.name}</strong>
                <small>{item.description || item.agentName}</small>
              </span>
            </Link>
          </li>
        ))}
        {pending.length > 0 && (
          <li className="bot-rail-group">{active.length ? "待启用" : "专职子助理"}</li>
        )}
        {visiblePending.map((item) => (
          <li key={item.specialistId}>
            <Link
              className="bot-rail-item"
              data-active={activeBot === item.specialistId ? "true" : undefined}
              href={`/assistant?bot=${encodeURIComponent(item.specialistId)}`}
              onClick={(event) => openBot(event, item.specialistId)}
            >
              <BotRailAvatar
                tone={specialistAppearance(item).color}
                shape={specialistAppearance(item).avatar}
              />
              <span className="bot-rail-label">
                <strong>{item.name}</strong>
                <small>{item.description || item.agentName}</small>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {creating && (
        <CreateBotDialog
          onClose={() => setCreating(false)}
          onCreated={(specialistId) => {
            window.location.assign(`/assistant?bot=${encodeURIComponent(specialistId)}`);
          }}
        />
      )}
      {pending.length > collapsedLimit && (
        <button
          type="button"
          className="bot-rail-more"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "收起" : "展开"}
        </button>
      )}
    </section>
  );
}
