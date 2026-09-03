"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
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
    .map((group) => ({
      ...group,
      agents: group.agents.toSorted((left, right) =>
        right.version.localeCompare(left.version, undefined, { numeric: true }),
      ),
    }))
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
}: {
  agents: readonly TaskAgent[];
  selected: TaskAgent | null;
  loading: boolean;
  currentTaskBusy: boolean;
  onChange: (agent: TaskAgent) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listboxId = useId();
  const groups = useMemo(() => groupTaskAgents(agents, query), [agents, query]);
  const disabled = loading || agents.length === 0;

  const closeMenu = useCallback((restoreTrigger = false) => {
    setOpen(false);
    setQuery("");
    if (restoreTrigger) {
      window.requestAnimationFrame(() => triggerRef.current?.focus());
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
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
      document.removeEventListener("mousedown", closeFromPointer);
      document.removeEventListener("focusin", closeFromFocus);
      document.removeEventListener("keydown", escape);
    };
  }, [closeMenu, open]);

  const choose = (agent: TaskAgent) => {
    const mode = taskAgentSwitchMode(selected, agent);
    if (mode === "version" && currentTaskBusy) return;
    closeMenu(true);
    if (mode !== "current") onChange(agent);
  };

  return (
    <div className="task-agent-switcher" ref={rootRef} data-open={open || undefined}>
      <button
        ref={triggerRef}
        className="task-agent-switcher-trigger"
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-controls={listboxId}
        aria-haspopup="listbox"
        aria-label="切换任务智能体或版本"
        title="同一智能体可在当前任务切换版本；切换其他智能体会创建新任务"
        onClick={() => {
          setQuery("");
          setOpen((current) => !current);
        }}
      >
        <span className="task-agent-switcher-mark" aria-hidden="true">
          <i />
          <i />
        </span>
        <span className="task-agent-switcher-copy">
          <small>当前智能体</small>
          <strong>
            {selected
              ? selected.domain === "historical"
                ? `${selected.displayName} · ${selected.version} · 已删除`
                : `${selected.displayName} · ${selected.version}`
              : (loading ? "正在读取…" : "暂无可用版本")}
          </strong>
        </span>
        <span className="task-agent-switcher-chevron" aria-hidden="true">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="task-agent-menu">
          <header>
            <div>
              <strong>选择智能体</strong>
              <small>{agents.length} 个已发布版本</small>
            </div>
            <span data-version-locked={currentTaskBusy || undefined}>
              {currentTaskBusy
                ? "当前任务运行中，版本暂锁定"
                : "同 Agent 换版本可续聊"}
            </span>
          </header>
          <label className="task-agent-search">
            <span aria-hidden="true" />
            <input
              ref={searchRef}
              type="search"
              value={query}
              placeholder="搜索名称、标识或版本"
              aria-label="搜索智能体"
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          <div className="task-agent-options" id={listboxId} role="listbox">
            {groups.length === 0 ? (
              <p className="task-agent-empty">没有匹配的智能体</p>
            ) : (
              groups.map((group) => (
                <section className="task-agent-group" key={group.key}>
                  {(() => {
                    const selectedKey = selected ? agentItemKey(selected) : "";
                    const activeAgent = group.agents.find(
                      (agent) => agentItemKey(agent) === selectedKey,
                    );
                    const preferred = activeAgent ?? group.agents[0];
                    const groupActive = Boolean(activeAgent);
                    const versionLocked = currentTaskBusy && groupActive;
                    return (
                      <>
                        <button
                          type="button"
                          role="option"
                          aria-selected={groupActive}
                          className={`task-agent-group-choice${groupActive ? " is-active" : ""}`}
                          onClick={() => choose(preferred)}
                        >
                          <span className="task-agent-group-copy">
                            <strong>{group.displayName}</strong>
                            <span>
                              {group.name} · {group.domain}
                            </span>
                          </span>
                          {group.agents.length === 1 && (
                            <small>{preferred.version}{groupActive ? " · 当前" : ""}</small>
                          )}
                        </button>
                        {group.agents.length > 1 && (
                          <label className="task-agent-version-select">
                            <select
                              aria-label={`${group.displayName} 版本`}
                              aria-describedby={versionLocked ? `${listboxId}-version-lock` : undefined}
                              disabled={versionLocked}
                              title={versionLocked ? "当前任务结束后可切换版本" : undefined}
                              value={agentItemKey(preferred)}
                              onChange={(event) => {
                                const next = group.agents.find(
                                  (agent) => agentItemKey(agent) === event.target.value,
                                );
                                if (next) choose(next);
                              }}
                            >
                              {group.agents.map((agent) => (
                                <option key={agentItemKey(agent)} value={agentItemKey(agent)}>
                                  {agent.version}
                                  {agentItemKey(agent) === selectedKey ? " · 当前" : ""}
                                </option>
                              ))}
                            </select>
                          </label>
                        )}
                      </>
                    );
                  })()}
                </section>
              ))
            )}
          </div>
          {currentTaskBusy && (
            <p className="task-agent-version-lock" id={`${listboxId}-version-lock`} role="status">
              当前任务完成或停止后可切换版本；选择其他智能体仍会创建新任务。
            </p>
          )}
        </div>
      )}
    </div>
  );
}
