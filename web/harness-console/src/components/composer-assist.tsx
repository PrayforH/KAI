"use client";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { COMPOSER_COMMANDS, composerTrigger } from "../lib/composer-interactions";
import { groupTaskAgents } from "./task-agent-switcher";
import type { TaskAgent } from "../lib/task-agent-catalog";
export function composerOptions(text: string, caret: number, agents: readonly TaskAgent[], skills: NonNullable<TaskAgent["skills"]> = []) {
  const trigger = composerTrigger(text, caret);
  if (!trigger) return [];
  if (trigger.symbol === "/") return COMPOSER_COMMANDS.filter((item) => `${item.name} ${item.label}`.toLowerCase().includes(trigger.query)).map((item) => ({ id: item.name, label: item.name, description: `${item.label} · ${item.description}`, agent: undefined as TaskAgent | undefined }));
  if (trigger.symbol === "@") return groupTaskAgents(agents, trigger.query).map((group) => ({ id: group.key, label: `@${group.displayName}`, description: "切换任务智能体 · 选择其他智能体会新建任务", agent: group.agents[0] }));
  const available = new Map(skills.map((skill) => [skill.name, skill] as const));
  return [...available.values()]
    .filter((skill) => `${skill.name} ${skill.description}`.toLowerCase().includes(trigger.query))
    .map((skill) => ({ id: `$${skill.name}`, label: `$${skill.name}`, description: skill.description, agent: undefined as TaskAgent | undefined }));
}
export function ComposerAssist({ options, index, onChoose }: { options: ReturnType<typeof composerOptions>; index: number; onChoose: (index: number) => void }) {
  const root = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(260);
  useLayoutEffect(() => {
    if (!options.length) return;
    const measure = () => {
      const bottom = root.current?.getBoundingClientRect().bottom;
      if (bottom !== undefined) setHeight(Math.max(48, Math.min(260, bottom - 12)));
    };
    measure();
    window.addEventListener("resize", measure);
    const observer = new ResizeObserver(measure);
    if (root.current?.parentElement) observer.observe(root.current.parentElement);
    return () => { window.removeEventListener("resize", measure); observer.disconnect(); };
  }, [options.length]);
  useEffect(() => { root.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({block: "nearest"}); }, [index]);
  if (!options.length) return null;
  return <div ref={root} style={{height}} className="composer-assist composer-assist-bounded" id="composer-suggestions" role="listbox" aria-label="输入快捷操作">
    <div className="composer-assist-options">{options.map((option, offset) => <button type="button" role="option" id={`composer-option-${offset}`} aria-selected={offset === index} key={option.id} onMouseDown={(event) => event.preventDefault()} onClick={() => onChoose(offset)}>
      <strong>{option.label}</strong><small>{option.description}</small>
    </button>)}</div>
    <footer>↑ ↓ 选择 · Enter 确认 · Esc 关闭</footer>
  </div>;
}
