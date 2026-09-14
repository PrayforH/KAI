"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  agentIdentity,
  agentItemKey,
  type TaskAgent,
} from "../lib/task-agent-catalog";

export interface TaskAgentGroup {
  key: string;
  name: string;
  displayName: string;
  domain: string;
  agents: TaskAgent[];
}

export type TaskAgentSwitchMode = "current" | "version" | "new-task";

export function taskAgentSwitchMode(
  selected: TaskAgent | null,
  next: TaskAgent,
): TaskAgentSwitchMode {
  if (!selected || agentIdentity(selected) !== agentIdentity(next)) {
    return "new-task";
  }
  return agentItemKey(selected) === agentItemKey(next) ? "current" : "version";
}

export function groupTaskAgents(
  agents: readonly TaskAgent[],
  query = "",
): TaskAgentGroup[] {
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matching = normalizedQuery
    ? agents.filter((agent) =>
        [agent.displayName, agent.name, agent.version, agent.domain, agent.spaceName]
          .join(" ")
          .toLocaleLowerCase()
          .includes(normalizedQuery),
      )
    : agents;
  const groups = new Map<string, TaskAgentGroup>();
  for (const agent of matching) {
    const groupKey = agentIdentity(agent);
    const group = groups.get(groupKey);
    if (group) {
      group.agents.push(agent);
    } else {
      groups.set(groupKey, {
        key: groupKey,
        name: agent.name,
        displayName: agent.displayName,
        domain: agent.domain,
        agents: [agent],
      });
    }
  }
  return [...groups.values()]
    .map((group) => {
      const sorted = group.agents.toSorted((left, right) =>
        right.version.localeCompare(left.version, undefined, { numeric: true }),
      );
      return { ...group, displayName: sorted[0].displayName, domain: sorted[0].domain, agents: sorted };
    })
    .toSorted((left, right) =>
      left.displayName.localeCompare(right.displayName, "zh-CN"),
    );
}

export function TaskAgentSwitcher({
  agents,
  selected,
  loading,
  currentTaskBusy,
  onChange,
  onRefresh,
  kind = "agent",
}: {
  agents: readonly TaskAgent[];
  selected: TaskAgent | null;
  loading: boolean;
  currentTaskBusy: boolean;
  onChange: (agent: TaskAgent) => void;
  onRefresh?: () => void;
  kind?: "agent" | "version";
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();
  const groups = useMemo(() => groupTaskAgents(agents), [agents]);
  const visibleGroups = groups.filter((group) => `${group.displayName} ${group.name} ${group.agents[0]?.spaceName ?? ""}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const versions = groups.find((group) => selected && group.key === agentIdentity(selected))?.agents ?? [];
  const [menuHeight, setMenuHeight] = useState(280);
  const [mobileLeft, setMobileLeft] = useState<number>();
  const disabled = loading || agents.length === 0;

  const closeMenu = useCallback((restoreTrigger = false) => {
    setOpen(false);
    setQuery("");
    if (restoreTrigger) {
      window.requestAnimationFrame(() => triggerRef.current?.focus());
    }
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    searchRef.current?.focus({ preventScroll: true });
    const measure = () => {
      const rect = rootRef.current?.getBoundingClientRect();
      if (rect) {
        setMenuHeight(Math.max(64, Math.min(280, kind === "version" ? window.innerHeight - rect.bottom - 16 : rect.top - 16)));
        setMobileLeft(window.innerWidth <= 760 ? 12 - rect.left : undefined);
      }
    };
    measure();
    window.addEventListener("resize", measure);
    const closeFromPointer = (event: MouseEvent) => {
      if (
        event.target instanceof Node &&
        !rootRef.current?.contains(event.target)
      ) {
        closeMenu();
      }
    };
    const closeFromFocus = (event: FocusEvent) => {
      if (
        event.target instanceof Node &&
        !rootRef.current?.contains(event.target)
      ) {
        closeMenu();
      }
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeMenu(true);
      }
    };
    document.addEventListener("mousedown", closeFromPointer);
    document.addEventListener("focusin", closeFromFocus);
    document.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("resize", measure);
      document.removeEventListener("mousedown", closeFromPointer);
      document.removeEventListener("focusin", closeFromFocus);
      document.removeEventListener("keydown", escape);
    };
  }, [closeMenu, open, kind]);

  const choose = (agent: TaskAgent) => {
    const mode = taskAgentSwitchMode(selected, agent);
    if (mode === "version" && currentTaskBusy) return;
    closeMenu(true);
    if (mode !== "current") onChange(agent);
  };

  return (
    <div className={`task-agent-switcher${kind === "version" ? " task-version-switcher" : ""}`} ref={rootRef} data-open={open || undefined}>
      <button
        ref={triggerRef}
        className="task-agent-switcher-trigger"
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-controls={listboxId}
        aria-haspopup="dialog"
        aria-label={kind === "version" ? "切换当前智能体版本" : "切换任务智能体"}
        title="同一智能体可在当前任务切换版本；切换其他智能体会创建新任务"
        onClick={() => {
          if (!open) onRefresh?.();
          setQuery("");
          setOpen((current) => !current);
        }}
      >
        {kind === "version" ? (
          <span className="task-agent-switcher-branch" aria-hidden="true">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <line x1="6" x2="6" y1="3" y2="15" />
              <circle cx="18" cy="6" r="3" />
              <circle cx="6" cy="18" r="3" />
              <path d="M18 9a9 9 0 0 1-9 9" />
            </svg>
          </span>
        ) : (
          <span className="task-agent-switcher-mark" aria-hidden="true">
            <i />
            <i />
          </span>
        )}
        <span className="task-agent-switcher-copy">
          <small>当前智能体</small>
          <strong>
            {kind === "version" ? (selected ? `v${selected.version}` : "版本") : selected?.displayName ?? (loading ? "正在读取…" : "选择智能体")}
          </strong>
        </span>
        <span className="task-agent-switcher-chevron" aria-hidden="true">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="task-agent-menu" role="dialog" aria-label={kind === "version" ? "选择版本" : "选择智能体"} id={listboxId} style={{height: menuHeight, left: mobileLeft}}>
          <label className="task-agent-search">
            <input ref={searchRef} type="search" value={query} placeholder={kind === "version" ? "搜索版本…" : "搜索智能体…"}
              aria-label={kind === "version" ? "搜索版本" : "搜索智能体"} onChange={(event) => setQuery(event.target.value)} />
          </label>
          <div className="task-agent-options">
            {kind === "version" ? versions.filter((agent) => agent.version.includes(query.trim())).map((agent) => (
              <button key={agentItemKey(agent)} type="button" className="task-agent-group-choice"
                aria-pressed={Boolean(selected && agentItemKey(selected) === agentItemKey(agent))}
                disabled={currentTaskBusy} onClick={() => choose(agent)}>
                <strong>v{agent.version}</strong><small>{agent.version === selected?.version ? "当前" : agent === versions[0] ? "最新" : ""}</small>
              </button>
            )) : visibleGroups.map((group) => {
              const preferred = group.agents[0];
              const groupActive = Boolean(selected && group.key === agentIdentity(selected));
              return <button key={group.key} type="button" className="task-agent-group-choice" aria-pressed={groupActive} onClick={() => choose(preferred)}>
                <span className="task-agent-group-copy"><strong>{preferred.displayName}</strong>
                {preferred.spaceName && <small>{preferred.spaceName}</small>}</span>
              </button>;
            })}
            {((kind === "version" && !versions.some((agent) => agent.version.includes(query.trim()))) || (kind === "agent" && !visibleGroups.length)) && <p className="task-agent-empty">没有匹配项</p>}
          </div>
          {kind === "version" && currentTaskBusy && <p className="task-agent-version-lock" role="status">当前任务运行中，版本暂锁定</p>}
        </div>
      )}
    </div>
  );
}
