"use client";

import { useCallback, useEffect, useLayoutEffect, useState } from "react";
import { peekClientRead, readClientResource, forgetClientRead } from "./client-read-cache";
import { projectClient, type ApiProject } from "./studio-client";

const groupsKey = (userId: string) => `harness:sidebar:${encodeURIComponent(userId)}:groups`;

/** Keep project expansion and “show more” choices across workspace remounts. */
export function useSidebarGroups(userId: string) {
  const key = groupsKey(userId);
  const [groups, setGroups] = useState<ReadonlySet<string>>(() => new Set());
  useLayoutEffect(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
      setGroups(new Set(Array.isArray(saved) ? saved.filter((item): item is string => typeof item === "string") : []));
    } catch { setGroups(new Set()); }
  }, [key]);
  const update = useCallback((change: (current: ReadonlySet<string>) => ReadonlySet<string>) => {
    setGroups((current) => {
      const next = change(current);
      try { localStorage.setItem(key, JSON.stringify([...next])); } catch { /* Keep in-memory state when storage is unavailable. */ }
      return next;
    });
  }, [key]);
  return [groups, update] as const;
}

/** Reuse the last project list while a new workspace revalidates it. */
export function useSidebarProjects(userId: string) {
  const key = `sidebar-projects:${userId}`;
  const [snapshot, setSnapshot] = useState(() => ({ key, projects: peekClientRead<ApiProject[]>(key) ?? [] }));
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    void readClientResource(key, () => projectClient.list(), 5000)
      .then((projects) => { if (active) setSnapshot({ key, projects }); })
      .catch(() => { /* A transient read failure must not erase the directory. */ });
    return () => { active = false; };
  }, [key, revision]);
  const refresh = useCallback(() => {
    forgetClientRead(key);
    setRevision((value) => value + 1);
  }, [key]);
  const projects = snapshot.key === key ? snapshot.projects : peekClientRead<ApiProject[]>(key) ?? [];
  return [projects, refresh] as const;
}
