"use client";

import { useState } from "react";
import {
  studioClient,
  type StudioAgentBuilderPatch,
} from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import styles from "./agent-studio.module.css";

export type CopilotBlockKey = "taskContract" | "systemPrompt" | "evaluationCases";

const BLOCK_LABELS: Record<CopilotBlockKey, string> = {
  taskContract: "任务契约",
  systemPrompt: "System Prompt",
  evaluationCases: "评测基线",
};

function lines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function CopilotDrawer({
  open,
  draft,
  canEdit,
  dirty,
  onClose,
  onApply,
  onSave,
}: {
  open: boolean;
  draft: StudioDraft;
  canEdit: boolean;
  dirty: boolean;
  onClose: () => void;
  onApply: (update: Partial<StudioDraft>, acceptedBlocks: number) => void;
  onSave: () => void;
}) {
  const contract = draft.taskContract;
  const [goal, setGoal] = useState(contract?.goal ?? "");
  const [audience, setAudience] = useState(contract?.audience ?? "当前用户");
  const [inputs, setInputs] = useState((contract?.inputs ?? []).join("\n"));
  const [outputs, setOutputs] = useState((contract?.outputs ?? []).join("\n"));
  const [constraints, setConstraints] = useState(
    (contract?.constraints ?? []).join("\n"),
  );
  const [patch, setPatch] = useState<StudioAgentBuilderPatch | null>(null);
  const [accepted, setAccepted] = useState<
    Record<CopilotBlockKey, boolean> | null
  >(null);
  const [appliedBlocks, setAppliedBlocks] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!open) return null;

  const formReady =
    goal.trim().length > 0 && lines(inputs).length > 0 && lines(outputs).length > 0;

  const beforeContract = JSON.stringify(draft.taskContract ?? {}, null, 2);
  const afterContract = patch
    ? JSON.stringify(patch.taskContract, null, 2)
    : "";
  const blockChanged: Record<CopilotBlockKey, boolean> = patch
    ? {
        taskContract: afterContract !== beforeContract,
        systemPrompt: patch.systemPrompt !== draft.systemPrompt,
        evaluationCases:
          JSON.stringify(patch.evaluationCases) !==
          JSON.stringify(draft.evalCases),
      }
    : { taskContract: false, systemPrompt: false, evaluationCases: false };
  const toggle = (key: CopilotBlockKey) =>
    setAccepted((current) =>
      current ? { ...current, [key]: !current[key] } : current,
    );
  const acceptedCount = accepted
    ? (Object.keys(accepted) as CopilotBlockKey[]).filter(
        (key) => accepted[key] && blockChanged[key],
      ).length
    : 0;

  async function requestPatch() {
    setBusy(true);
    setError("");
    try {
      const result = await studioClient.createAgentBuilderPatch(draft.id, {
        expectedRevision: draft.revision,
        goal: goal.trim(),
        audience: audience.trim() || "当前用户",
        inputs: lines(inputs),
        outputs: lines(outputs),
        constraints: lines(constraints),
      });
      setPatch(result);
      setAccepted({
        taskContract: afterContractChanged(result),
        systemPrompt: result.systemPrompt !== draft.systemPrompt,
        evaluationCases:
          JSON.stringify(result.evaluationCases) !==
          JSON.stringify(draft.evalCases),
      });
      setAppliedBlocks(0);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Patch 生成失败");
    } finally {
      setBusy(false);
    }

    function afterContractChanged(candidate: StudioAgentBuilderPatch) {
      return (
        JSON.stringify(candidate.taskContract ?? null) !==
        JSON.stringify(draft.taskContract ?? null)
      );
    }
  }

  function applyAccepted() {
    if (!patch || !accepted) return;
    const update: Partial<StudioDraft> = {};
    let count = 0;
    if (accepted.taskContract && blockChanged.taskContract) {
      update.taskContract = patch.taskContract;
      count += 1;
    }
    if (accepted.systemPrompt && blockChanged.systemPrompt) {
      update.systemPrompt = patch.systemPrompt;
      count += 1;
    }
    if (accepted.evaluationCases && blockChanged.evaluationCases) {
      update.evalCases = patch.evaluationCases;
      count += 1;
    }
    if (count === 0) return;
    setAppliedBlocks(count);
    onApply(update, count);
  }

  return (
    <>
      <button
        type="button"
        className={styles.contractBackdrop}
        aria-label="关闭 Copilot"
        onClick={onClose}
      />
      <aside
        className={styles.contractRail}
        id="copilot-drawer"
        aria-label="Agent Copilot"
        role="dialog"
        aria-modal="true"
        data-open={open}
      >
        <div className={styles.contractHeader}>
          <div>
            <span>AGENT COPILOT</span>
            <strong>按块审阅的构建补丁</strong>
          </div>
          <button type="button" aria-label="关闭 Copilot" onClick={onClose}>
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="m4.5 4.5 7 7m0-7-7 7" />
            </svg>
          </button>
        </div>

        <div className={styles.copilotBody}>
          <p className={styles.copilotIntro}>
            描述目标、输入与输出，Copilot 生成整份可审阅 Patch：任务契约、
            System Prompt 与评测基线。Patch 不直接写入草稿——每个块单独接受。
          </p>
          <label className={styles.copilotField}>
            <span>目标（goal）</span>
            <textarea
              rows={2}
              value={goal}
              disabled={!canEdit}
              onChange={(event) => setGoal(event.target.value)}
              placeholder="例如：整理指定公司的公开信息，给出投资风险摘要。"
            />
          </label>
          <label className={styles.copilotField}>
            <span>输入（每行一条）</span>
            <textarea
              rows={2}
              value={inputs}
              disabled={!canEdit}
              onChange={(event) => setInputs(event.target.value)}
              placeholder={"公司名称\n调研时间范围"}
            />
          </label>
          <label className={styles.copilotField}>
            <span>输出（每行一条）</span>
            <textarea
              rows={2}
              value={outputs}
              disabled={!canEdit}
              onChange={(event) => setOutputs(event.target.value)}
              placeholder={"投资风险摘要\n证据链接清单"}
            />
          </label>
          <label className={styles.copilotField}>
            <span>边界约束（每行一条，可选）</span>
            <textarea
              rows={2}
              value={constraints}
              disabled={!canEdit}
              onChange={(event) => setConstraints(event.target.value)}
              placeholder="不得给出未经来源核验的结论"
            />
          </label>
          <div className={styles.copilotActions}>
            <button
              type="button"
              disabled={!canEdit || !draft.id || dirty || busy || !formReady}
              title={
                dirty
                  ? "先保存当前修改，Copilot 才能基于最新 revision 生成 Patch"
                  : undefined
              }
              onClick={() => void requestPatch()}
            >
              {busy ? "正在生成 Patch…" : "生成 Patch"}
            </button>
          </div>
          {error && <p className={styles.copilotError} role="alert">{error}</p>}

          {patch && (
            <>
              <div className={styles.copilotExplanation}>
                <span>生成说明</span>
                <ul>
                  {patch.explanation.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
                <small>
                  服务端校验：{patch.validation.ready ? "Patch 后草稿可过结构门禁" : "仍有阻塞项，接受后请查看阻塞原因"}
                  {" · "}基准 revision {patch.baseRevision}
                </small>
              </div>
              <section className={styles.copilotBlocks}>
                {(Object.keys(BLOCK_LABELS) as CopilotBlockKey[]).map((key) => (
                  <article key={key} data-changed={blockChanged[key]}>
                    <header>
                      <strong>{BLOCK_LABELS[key]}</strong>
                      {blockChanged[key] ? (
                        <label>
                          <input
                            type="checkbox"
                            checked={Boolean(accepted?.[key])}
                            disabled={!canEdit}
                            onChange={() => toggle(key)}
                          />
                          <span>接受此块</span>
                        </label>
                      ) : (
                        <em>与当前草稿一致</em>
                      )}
                    </header>
                    {key === "taskContract" && (
                      <div className={styles.copilotDiffPair}>
                        <pre>{beforeContract}</pre>
                        <pre>{afterContract}</pre>
                      </div>
                    )}
                    {key === "systemPrompt" && (
                      <div className={styles.copilotDiffPair}>
                        <pre>{draft.systemPrompt}</pre>
                        <pre>{patch.systemPrompt}</pre>
                      </div>
                    )}
                    {key === "evaluationCases" && (
                      <div className={styles.copilotDiffPair}>
                        <pre>{`${draft.evalCases.length} 条基线`}</pre>
                        <pre>
                          {patch.evaluationCases
                            .map((item) => `[${item.tag}] ${item.prompt}`)
                            .join("\n")}
                        </pre>
                      </div>
                    )}
                  </article>
                ))}
              </section>
              <div className={styles.copilotActions}>
                <button
                  type="button"
                  disabled={!canEdit || acceptedCount === 0 || busy}
                  onClick={applyAccepted}
                >
                  应用已选块（{acceptedCount}）
                </button>
                <button
                  type="button"
                  disabled={!canEdit || appliedBlocks === 0 || busy}
                  onClick={() => {
                    onSave();
                    onClose();
                  }}
                >
                  保存并检查
                </button>
              </div>
              {appliedBlocks > 0 && (
                <p className={styles.copilotIntro} role="status">
                  已按块应用 {appliedBlocks} 块到当前草稿；保存前不会进入服务端。
                </p>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
