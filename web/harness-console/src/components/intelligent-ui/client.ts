"use client";

import type { FieldValues } from "./form-view";
import type { Presentation, PresentationAction } from "./schema";

export type UiExecution = {
  request_id: string;
  action_id: string;
  revision: number;
  run_id: string | null;
  execution_status: string;
  error_code?: string | null;
  submitted_values?: FieldValues;
};
export type UiViewState = {
  sort?: { key: string; descending: boolean } | null;
  show_data?: boolean;
  values?: FieldValues;
  html_state?: Record<string, unknown>;
};
export type UiSnapshot = Presentation & {
  view_state: { version: number; state: UiViewState };
  action_requests: UiExecution[];
};

export const uiPath = (id: string) =>
  `/api/ui/presentations/${encodeURIComponent(id)}`;

export class UiRequestError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
  }
}

export async function uiJson<T>(url: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-store", ...init });
  } catch (cause) {
    if (init?.signal?.aborted) throw cause;
    throw new UiRequestError(0, "网络连接暂时中断，请重试。当前输入仍保留在此页。");
  }
  if (!response.ok) {
    let detail = "操作失败，请重试";
    try {
      const body = await response.json();
      detail = body.error?.message || body.detail || detail;
    } catch {
      /* preserve an actionable default */
    }
    if (response.status === 409)
      detail = "此界面或操作状态已变更。输入已保留，请刷新核对后再操作。";
    if (response.status === 401) detail = "登录已过期，请重新登录。";
    if (response.status === 404) detail = "交互工具暂时无法恢复，请重试。原始文件仍可从产出中打开。";
    if (response.status === 425) detail = "交互工具正在准备，请稍后重试。";
    throw new UiRequestError(response.status,
      typeof detail === "string" ? detail : "请检查填写内容后重试",
    );
  }
  return response.json() as Promise<T>;
}

/** Retry only snapshot reads; action submissions and state writes stay explicit. */
export function loadUiSnapshot(p: Presentation, signal: AbortSignal): Promise<UiSnapshot> {
  const url = `${uiPath(p.presentation_id)}?revision=${p.revision}&run_id=${encodeURIComponent(p.run_id)}`;
  async function read(attempt = 0): Promise<UiSnapshot> {
    if (signal.aborted) throw signal.reason;
    try {
      return await uiJson<UiSnapshot>(url, { signal });
    } catch (cause) {
      if (!(cause instanceof UiRequestError) || ![0, 404, 425, 502, 503, 504].includes(cause.status) || attempt >= 2) throw cause;
      await new Promise<void>(resolve => setTimeout(resolve, attempt === 0 ? 250 : 750));
      return read(attempt + 1);
    }
  }
  return read();
}

export function submitUiAction(
  p: Presentation,
  action: PresentationAction,
  values: FieldValues,
  key: string,
) {
  return uiJson<UiExecution>(`${uiPath(p.presentation_id)}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": key },
    body: JSON.stringify({
      action_id: action.action_id,
      expected_revision: p.revision,
      values,
    }),
  });
}

export const terminalExecution = (status: string) =>
  ["succeeded", "failed", "cancelled", "rejected", "timed_out"].includes(
    status,
  );
export const executionLabel = (status: string) =>
  ({
    accepted: "请求已接收",
    dispatching: "正在准备",
    submitted: "请求已接收",
    queued: "排队中",
    provisioning: "正在准备",
    running: "执行中",
    waiting_approval: "等待你审批",
    succeeded: "执行完成",
    failed: "执行失败",
    cancelled: "已取消",
    rejected: "已拒绝",
    timed_out: "执行超时",
  })[status] ?? "正在确认执行状态";
