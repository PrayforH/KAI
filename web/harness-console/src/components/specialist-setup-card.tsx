"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { assistantRequest, type Specialist } from "../lib/assistant-client";
import { SpecialistCredentials } from "./specialist-credentials";

type Detail = {
  specialist: Specialist;
  threadId: string | null;
  primaryObjectiveId: string | null;
  duties?: { objectiveId: string; hasExecutions: boolean }[];
};

const SETUP_STATUSES = new Set(["draft", "needs_input", "validating", "ready", "blocked", "active"]);

/**
 * 转交后缺配置的补齐卡（/assistant 子助理会话内）：
 * 能力配置就绪后显式开始当前交办；不创建周期规则、不批量启动旧职责。
 * 首个执行入队；成功后卡片变成「本次交办已安排」并刷新 bot 状态。
 */
export function SpecialistSetupCard({
  specialistId,
  onReady,
  objectiveId,
  onAlreadyActive,
}: {
  specialistId: string;
  onReady?: () => void;
  objectiveId?: string | null;
  /** Only a real execution, never Bot availability alone, settles a handover. */
  onAlreadyActive?: () => void;
}) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(false);
  // 只在本次点击后显示「本次交办已安排」；刷新后由真实执行记录判断，
  // 避免把一次性的成功回执变成常驻横幅（那时首跑可能早跑完了）。
  const [justEnabled, setJustEnabled] = useState(false);
  const [error, setError] = useState("");
  // 保存流程本身会看到 active，避免和 onReady 重复上报。
  const savingRef = useRef(false);
  const announcedActiveRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const data = await assistantRequest<Detail>(
        `specialists/${encodeURIComponent(specialistId)}`,
      );
      setDetail(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "读取子助理配置失败");
    }
  }, [specialistId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const targetId = objectiveId || detail?.primaryObjectiveId;
    if (!detail || savingRef.current || announcedActiveRef.current) return;
    if (!detail.duties?.some(duty => duty.objectiveId === targetId && duty.hasExecutions)) return;
    announcedActiveRef.current = true;
    onAlreadyActive?.();
  }, [detail, objectiveId, onAlreadyActive]);

  async function saveAndEnable() {
    if (!detail || busy) return;
    const targetId = objectiveId || detail.primaryObjectiveId;
    if (!targetId || !detail.duties?.some(duty => duty.objectiveId === targetId)) {
      setError("这项交办已变更，请刷新后重试。");
      return;
    }
    savingRef.current = true;
    setBusy(true);
    setError("");
    try {
      // Capability readiness does not create a trigger rule.
      const configured = await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/config`,
        "POST",
        {
          config: {
            ...(detail.specialist.config as Record<string, unknown>),
            notify_channel: "inbox",
          },
          expectedRevision: detail.specialist.revision,
        },
      );
      // Enable availability, then explicitly start only this handover once.
      if (configured.status !== "active") await assistantRequest<Specialist>(
        `specialists/${encodeURIComponent(specialistId)}/status`,
        "POST",
        { status: "active", expectedRevision: configured.revision },
      );
      await assistantRequest(
        `objectives/${encodeURIComponent(targetId)}/executions`, "POST", undefined,
        `first-handover:${targetId}`,
      );
      setJustEnabled(true);
      await load();
      onReady?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存失败，请稍后重试");
    } finally {
      savingRef.current = false;
      setBusy(false);
    }
  }

  if (!detail) {
    return error ? (
      <div className="specialist-setup-card" role="alert">
        <p className="specialist-setup-error">{error}</p>
      </div>
    ) : null;
  }

  if (justEnabled) {
    return (
      <div className="specialist-setup-card" data-state="enabled">
        <p className="specialist-setup-done">本次交办已安排</p>
        <p className="specialist-setup-hint">
          结果以该任务的执行记录为准；不会因此建立定时任务。
        </p>
      </div>
    );
  }

  const status = detail.specialist.status;
  if (!SETUP_STATUSES.has(status)) return null;
  if (detail.duties?.some(duty => duty.objectiveId === (objectiveId || detail.primaryObjectiveId) && duty.hasExecutions)) return null;
  if (!detail.primaryObjectiveId) {
    if (status === "ready") return null;
    return (
      <div className="specialist-setup-card" data-state="no-duty">
        <p className="specialist-setup-title">可以直接和这个助理对话</p>
        <p className="specialist-setup-hint">
          它会在收到消息或明确交办后工作；定时规则可以为具体任务单独设置。
        </p>
      </div>
    );
  }

  return (
    <div className="specialist-setup-card" role="group" aria-label="补齐子助理配置">
      <p className="specialist-setup-title">开始这次交办</p>
      <p className="specialist-setup-hint">
        按需接单，只安排这一次交办；不会为其他任务创建或修改定时规则。
      </p>
      {error && <p className="specialist-setup-error" role="alert">{error}</p>}
      <button
        type="button"
        className="specialist-setup-submit"
        disabled={busy}
        onClick={() => void saveAndEnable()}
      >
        {busy ? "正在安排…" : "开始这次交办"}
      </button>
      <details className="specialist-setup-credentials">
        <summary>可选：为这个 Bot 添加凭据</summary>
        <SpecialistCredentials
          specialistId={specialistId}
          specialist={detail.specialist}
          onUpdated={() => void load()}
          compact
        />
      </details>
    </div>
  );
}
