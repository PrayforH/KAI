"use client";

import { useEffect, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { PRODUCT_NAME } from "./product-brand";

const HELP_MANUAL_URL = "https://my.feishu.cn/docx/DdiCdPFcroUpUXxOumNcQpIin1g";

const PHASE_LABELS: Record<string, string> = {
  idle: "空闲",
  queued: "排队中",
  running: "运行中",
  waiting_approval: "待审批",
  cancelling: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "失败",
  rejected: "已拒绝",
  timed_out: "已超时",
  unknown: "未知",
};

const SCOPE_LABELS: Record<string, string> = {
  personal: "个人",
  team: "团队",
  historical: "历史",
  restored: "本地",
};

function CloseIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="m4 4 8 8M12 4l-8 8" />
    </svg>
  );
}

interface RailFile {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes?: number | null;
  thread_id?: string;
}

function formatFileSize(value?: number | null) {
  if (value === null || value === undefined) return "";
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(value < 10_240 ? 1 : 0)} KB`;
  return `${(value / 1_048_576).toFixed(1)} MB`;
}

export function WorkbenchRail({
  open,
  onClose,
  taskTitle,
  agentDisplay,
  agentKey,
  agentScope,
  modelRoute,
  runPhase,
  threadId,
}: {
  open: boolean;
  onClose: () => void;
  taskTitle: string;
  agentDisplay: string;
  agentKey: string;
  agentScope: string;
  modelRoute: string | null;
  runPhase: string | null;
  threadId: string;
}) {
  const [host, setHost] = useState("");
  const [files, setFiles] = useState<RailFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(true);

  useEffect(() => {
    setHost(window.location.host);
  }, []);

  useEffect(() => {
    if (!threadId) {
      setFiles([]);
      setFilesLoading(false);
      return;
    }
    const controller = new AbortController();
    setFilesLoading(true);
    async function load() {
      try {
        const response = requireAuthenticatedResponse(
          await fetch("/api/harness/artifacts?limit=500", {
            cache: "no-store",
            signal: controller.signal,
          }),
        );
        if (!response.ok) throw new Error();
        const all = (await response.json()) as RailFile[];
        setFiles(all.filter((file) => file.thread_id === threadId));
      } catch {
        if (!controller.signal.aborted) setFiles([]);
      } finally {
        if (!controller.signal.aborted) setFilesLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [threadId]);

  const phaseKnown = runPhase && runPhase in PHASE_LABELS;
  const phaseKey = (phaseKnown ? runPhase : "unknown") as
    | keyof typeof PHASE_LABELS
    | "unknown";
  const scopeLabel = SCOPE_LABELS[agentScope] ?? agentScope;

  return (
    <aside
      className="workbench-rail"
      aria-hidden={open ? undefined : true}
      aria-label="任务上下文"
      tabIndex={-1}
    >
      <div className="workbench-rail-panel" aria-hidden={!open}>
        <header className="workbench-rail-header">
          <strong>任务上下文</strong>
          <button
            type="button"
            className="workbench-rail-close"
            aria-label="收起任务上下文"
            title="收起任务上下文"
            onClick={onClose}
          >
            <CloseIcon />
          </button>
        </header>
        <div className="workbench-rail-body">
          <section className="workbench-rail-section">
            <small>当前任务</small>
            <strong className="workbench-rail-task">{taskTitle}</strong>
          </section>
          <section className="workbench-rail-section">
            <small>文件</small>
            {filesLoading ? (
              <span className="workbench-rail-files-empty">正在读取文件…</span>
            ) : files.length === 0 ? (
              <span className="workbench-rail-files-empty">暂无文件</span>
            ) : (
              <div className="workbench-rail-files">
                {files.map((file) => {
                  const previewable =
                    file.media_type.startsWith("text/") ||
                    file.media_type.startsWith("image/") ||
                    file.media_type === "application/json" ||
                    file.media_type === "application/pdf";
                  return (
                    <a
                      key={file.artifact_id}
                      className="workbench-rail-file"
                      href={`/api/harness/artifacts/${encodeURIComponent(file.artifact_id)}${previewable ? "?preview=1" : ""}`}
                      target={previewable ? "_blank" : undefined}
                      rel={previewable ? "noreferrer" : undefined}
                      download={previewable ? undefined : file.name}
                      title={previewable ? `预览 ${file.name}` : `下载 ${file.name}`}
                    >
                      <span className="workbench-rail-file-name">{file.name}</span>
                      <span className="workbench-rail-file-size">
                        {formatFileSize(file.size_bytes)}
                      </span>
                    </a>
                  );
                })}
              </div>
            )}
          </section>
          <section className="workbench-rail-section">
            <small>智能体</small>
            <div className="workbench-rail-rows">
              <span className="workbench-rail-agent">{agentDisplay}</span>
              <code>{agentKey}</code>
            </div>
          </section>
          <section className="workbench-rail-section">
            <small>归属</small>
            <span className="workbench-rail-chip">{scopeLabel}</span>
          </section>
          <section className="workbench-rail-section">
            <small>模型路由</small>
            <span className="workbench-rail-rows">
              <code>{modelRoute ?? "默认（跟随智能体）"}</code>
            </span>
          </section>
          <section className="workbench-rail-section">
            <small>运行阶段</small>
            <span className={`workbench-rail-phase is-${phaseKey}`}>
              <i aria-hidden="true" />
              {PHASE_LABELS[phaseKey] ?? phaseKey}
            </span>
          </section>
          <section className="workbench-rail-section">
            <small>会话 ID</small>
            <code className="workbench-rail-thread">{threadId}</code>
          </section>
          <section className="workbench-rail-section">
            <small>环境</small>
            <code className="workbench-rail-thread">{host || "—"}</code>
          </section>
          <a
            className="workbench-rail-manual"
            href={HELP_MANUAL_URL}
            target="_blank"
            rel="noreferrer"
          >
            产品使用手册
          </a>
          <footer className="workbench-rail-footer">
            <span>{PRODUCT_NAME}</span>
          </footer>
        </div>
      </div>
    </aside>
  );
}
