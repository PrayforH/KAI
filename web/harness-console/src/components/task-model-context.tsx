"use client";

import { createContext, useContext, type ReactNode } from "react";
import type { TaskModelRoute } from "../lib/task-model-catalog";

export interface PlaygroundModelSelection {
  routes: TaskModelRoute[];
  routeId: string | null;
  onChange: (routeId: string | null) => void;
}

interface TaskModelContextValue {
  independent?: boolean;
  routes: TaskModelRoute[];
  agentDefaultRouteId: string | null;
  overrideRouteId: string | null;
  setOverrideRouteId: (routeId: string | null) => void;
}

const TaskModelContext = createContext<TaskModelContextValue>({
  routes: [],
  agentDefaultRouteId: null,
  overrideRouteId: null,
  setOverrideRouteId: () => undefined,
});

export function TaskModelProvider({
  routes,
  agentDefaultRouteId,
  overrideRouteId,
  onOverrideChange,
  independent = false,
  children,
}: {
  routes: TaskModelRoute[];
  agentDefaultRouteId: string | null;
  overrideRouteId: string | null;
  onOverrideChange: (routeId: string | null) => void;
  independent?: boolean;
  children: ReactNode;
}) {
  return (
    <TaskModelContext.Provider
      value={{
        routes,
        independent,
        agentDefaultRouteId,
        overrideRouteId,
        setOverrideRouteId: onOverrideChange,
      }}
    >
      {children}
    </TaskModelContext.Provider>
  );
}

export function useTaskModel() {
  return useContext(TaskModelContext);
}

export function TaskModelControl({
  disabled,
}: {
  disabled: boolean;
}) {
  const {
    routes,
    agentDefaultRouteId,
    overrideRouteId,
    setOverrideRouteId,
    independent,
  } = useTaskModel();
  if (routes.length === 0) return null;
  const effectiveOverrideRouteId =
    !independent && overrideRouteId === agentDefaultRouteId ? null : overrideRouteId;
  const selected = routes.find((route) => route.id === effectiveOverrideRouteId);
  const agentDefault = routes.find((route) => route.id === agentDefaultRouteId);
  const overrideRoutes = routes.filter(
    (route) => independent || route.id !== agentDefaultRouteId,
  );
  return (
    <div
      className="task-model-control"
      data-overridden={selected ? "true" : "false"}
    >
      <label>
        <span className="task-model-control-mark" aria-hidden="true">
          <i />
          <i />
        </span>
        <select
          value={effectiveOverrideRouteId ?? ""}
          disabled={disabled}
          aria-label="选择本次任务使用的模型"
          onChange={(event) => setOverrideRouteId(event.target.value || null)}
        >
          {(!independent || !selected) && <option value="" disabled={independent}>
            {independent ? "选择模型" : agentDefault?.label ?? "Agent 默认模型"}
          </option>}
          {overrideRoutes.map((route) => (
            <option key={route.id} value={route.id}>
              {route.label}
            </option>
          ))}
        </select>
        <span className="task-model-control-chevron" aria-hidden="true">
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
          </svg>
        </span>
      </label>
      <span className="task-model-control-status">
        {independent ? "当前对话" : selected?.modelType === "video_generation" ? "视频生成" : selected ? "仅本次任务" : "跟随 Agent"}
      </span>
    </div>
  );
}

export function TaskModelVisionNotice({
  disabled,
  requiresVision,
}: {
  disabled: boolean;
  requiresVision: boolean;
}) {
  const {
    routes,
    agentDefaultRouteId,
    overrideRouteId,
    setOverrideRouteId,
  } = useTaskModel();
  const selected = routes.find((route) => route.id === overrideRouteId);
  const agentDefault = routes.find((route) => route.id === agentDefaultRouteId);
  const effectiveRoute = selected ?? agentDefault;
  const visionRoute = routes.find((route) => route.capabilities.includes("vision"));
  const needsVisionSwitch =
    requiresVision &&
    effectiveRoute?.modelType !== "video_generation" &&
    !effectiveRoute?.capabilities.includes("vision") &&
    Boolean(visionRoute);
  if (!needsVisionSwitch || !visionRoute) return null;
  return (
    <div className="task-model-vision-notice" role="status">
      <span>当前模型不处理图片内容</span>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOverrideRouteId(visionRoute.id)}
      >
        本次切换到 {visionRoute.label}
      </button>
    </div>
  );
}
