"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { COMPOSER_COMMANDS, composerTrigger } from "../lib/composer-interactions";
import { MENTION_PREFIX, type MentionedAssistant } from "../lib/bot-mentions";
import { BotRailAvatar, botAccent } from "./bot-rail";
import type { TaskAgent } from "../lib/task-agent-catalog";
export function composerOptions(
  text: string,
  caret: number,
  assistants: readonly MentionedAssistant[] = [],
  skills: NonNullable<TaskAgent["skills"]> = [],
) {
  const trigger = composerTrigger(text, caret);
  if (!trigger) return [];
  if (trigger.symbol === "/") return COMPOSER_COMMANDS.filter((item) => `${item.name} ${item.label}`.toLowerCase().includes(trigger.query)).map((item) => ({ id: item.name, label: item.name, description: `${item.label} · ${item.description}` }));
  if (trigger.symbol === "@") {
    return assistants
      .filter((bot) => `${bot.name} ${bot.description ?? ""}`.toLowerCase().includes(trigger.query))
      .map((bot) => ({
        id: `${MENTION_PREFIX}${bot.specialistId}`,
        label: bot.name,
        bot,
      }));
  }
  const available = new Map(skills.map((skill) => [skill.name, skill] as const));
  return [...available.values()]
    .filter((skill) => `${skill.name} ${skill.description}`.toLowerCase().includes(trigger.query))
    .map((skill) => ({ id: `$${skill.name}`, label: `$${skill.name}`, description: skill.description }));
}
export function ComposerAssist({ options, index, onChoose }: { options: ReturnType<typeof composerOptions>; index: number; onChoose: (index: number) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const [maxHeight, setMaxHeight] = useState(260);
  useLayoutEffect(() => {
    if (!options.length) return;
    const measure = () => {
      const bottom = root.current?.getBoundingClientRect().bottom;
      if (bottom !== undefined) setMaxHeight(Math.max(48, Math.min(260, bottom - 12)));
    };
    measure();
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    if (root.current?.parentElement) observer.observe(root.current.parentElement);
    return () => { window.removeEventListener("resize", measure); observer.disconnect(); };
  }, [options.length]);
  useEffect(() => {
    const list = root.current?.querySelector<HTMLElement>(".composer-assist-options");
    const selected = list?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!list || !selected) return;
    const item = selected.getBoundingClientRect();
    const viewport = list.getBoundingClientRect();
    if (item.top < viewport.top) list.scrollTop += item.top - viewport.top;
    else if (item.bottom > viewport.bottom) list.scrollTop += item.bottom - viewport.bottom;
  }, [index, options.length]);
  if (!options.length) return null;
  return <div ref={root} style={{maxHeight}} className="composer-assist composer-assist-bounded" data-mentions={options[0] && "bot" in options[0] ? true : undefined} id="composer-suggestions" role="listbox" aria-label="输入快捷操作">
    <div className="composer-assist-options">{options.map((option, offset) => <button type="button" role="option" id={`composer-option-${offset}`} aria-selected={offset === index} key={option.id} onMouseDown={(event) => event.preventDefault()} onClick={() => onChoose(offset)}>
      {"bot" in option ? <>
        <BotRailAvatar tone={option.bot.appearance?.color ?? botAccent(option.bot.specialistId)} shape={option.bot.appearance?.avatar} />
        <strong title={option.label}>{option.label}</strong>
      </> : <><strong>{option.label}</strong><small>{option.description}</small></>}
    </button>)}</div>
    <footer>↑ ↓ 选择 · Enter 确认 · Esc 关闭</footer>
  </div>;
}
