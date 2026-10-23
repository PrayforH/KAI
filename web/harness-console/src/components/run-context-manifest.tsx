"use client";

import { useEffect, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import styles from "./run-context-manifest.module.css";

interface SkillSource {
  name: string;
  source: string;
  content_hash?: string;
  contentHash?: string;
  resolution?: string;
}
interface ContextProvenance {
  manifest: {
    agent: { name: string; version: string; ownerUserId: string };
    project: {
      name: string;
      updatedAt: string;
      documents: Array<{ id: string; name: string; sha256: string | null }>;
      memories: Array<{ id: string; contentHash: string; updatedAt: string }>;
    };
    skills: SkillSource[];
    knowledge: Array<{
      knowledgeBaseReference: string;
      sourceReference: string;
      snapshotId?: string;
    }>;
  } | null;
  skillResolutions: SkillSource[];
}

export function RunContextManifest({ runId }: { runId: string }) {
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [value, setValue] = useState<ContextProvenance | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setValue(null);
    void (async () => {
      try {
        const response = requireAuthenticatedResponse(
          await fetch(
            `/api/harness/runs/${encodeURIComponent(runId)}/context`,
            { cache: "no-store", signal: controller.signal },
          ),
        );
        if (!response.ok) throw new Error("资源来源暂时无法读取，请重试。");
        const payload = (await response.json()) as ContextProvenance;
        if (!controller.signal.aborted) setValue(payload);
      } catch (cause) {
        if (!controller.signal.aborted)
          setError(cause instanceof Error ? cause.message : "读取失败");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [open, revision, runId]);
  const manifest = value?.manifest;
  const resolutions = new Map(
    value?.skillResolutions.map((skill) => [skill.name, skill]),
  );
  return (
    <details
      className={styles.panel}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>本次生效资源</summary>
      {open && (
        <div className={styles.body}>
          {loading && <p role="status">正在读取资源来源…</p>}
          {error && <p role="alert">{error}</p>}
          {value && !manifest && <p>这个历史任务没有保存资源清单。</p>}
          {manifest && (
            <>
              <p>
                执行智能体：{manifest.agent.name} · {manifest.agent.version}
              </p>
              <p>
                项目：{manifest.project.name} ·{" "}
                {new Date(manifest.project.updatedAt).toLocaleString("zh-CN")}
              </p>
              <p>
                {manifest.project.documents.length} 份资料 ·{" "}
                {manifest.project.memories.length} 条项目事实 ·{" "}
                {manifest.knowledge.length} 个知识快照
              </p>
              <ul>
                {manifest.project.documents.map((document) => (
                  <li key={document.id}>
                    <strong>{document.name}</strong>
                    <span>项目资料</span>
                    <code title={document.sha256 ?? undefined}>
                      {document.sha256?.slice(0, 12) ??
                        "历史资料未记录内容哈希"}
                    </code>
                  </li>
                ))}
                {manifest.knowledge.map((binding) => (
                  <li
                    key={`${binding.knowledgeBaseReference}:${binding.sourceReference}`}
                  >
                    <strong>{binding.knowledgeBaseReference}</strong>
                    <span>知识来源：{binding.sourceReference}</span>
                    <code>{binding.snapshotId}</code>
                  </li>
                ))}
              </ul>
              <ul>
                {manifest.skills.map((skill) => {
                  const resolved = resolutions.get(skill.name);
                  return (
                    <li key={skill.source}>
                      <strong>{skill.name}</strong>
                      <span>{skill.source.replace(/^project:/, "")}</span>
                      <span>
                        {resolved?.resolution === "agent_precedence"
                          ? "同名冲突，使用智能体自带版本"
                          : resolved
                            ? "使用项目附加版本"
                            : "已固定，等待执行解析"}
                      </span>
                      <code title={skill.content_hash}>
                        {skill.content_hash?.slice(0, 12)}
                      </code>
                    </li>
                  );
                })}
              </ul>
              {manifest.skills.length === 0 && (
                <p>技能随智能体发布版本提供，项目未额外添加。</p>
              )}
              <p>
                资料与事实按开始执行时的版本保存，之后的项目编辑不会改写本次记录。
              </p>
            </>
          )}
          <button
            type="button"
            onClick={() => setRevision((current) => current + 1)}
            disabled={loading}
          >
            刷新来源
          </button>
        </div>
      )}
    </details>
  );
}
