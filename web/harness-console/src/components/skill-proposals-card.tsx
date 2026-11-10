"use client";

import { useCallback, useEffect, useState } from "react";
import { assistantRequest } from "../lib/assistant-client";
import type { PersonalSkill, SkillProposal } from "../lib/assistant-client";
import styles from "./skill-proposals-card.module.css";

// 复用 assistantRequest，避免为侧栏再引入一套客户端；路径与后端保持一致。
const listSkillProposals = () => assistantRequest<SkillProposal[]>("skill-proposals");
const listPersonalSkills = () => assistantRequest<PersonalSkill[]>("personal-skills");
const generateSkillProposal = (id: string) => assistantRequest<SkillProposal>(
  `skill-proposals/${encodeURIComponent(id)}/generate`, "POST", {},
);
const saveSkillProposal = (id: string, expectedRevision: number) => assistantRequest<PersonalSkill>(
  `skill-proposals/${encodeURIComponent(id)}/save`, "POST", { expectedRevision },
);
const dismissSkillProposal = (id: string, expectedRevision: number) => assistantRequest<SkillProposal>(
  `skill-proposals/${encodeURIComponent(id)}/dismiss`, "POST", { expectedRevision },
);
const setPersonalSkillEnabled = (id: string, enabled: boolean, expectedRevision: number) =>
  assistantRequest<PersonalSkill>(`personal-skills/${encodeURIComponent(id)}/enabled`, "POST", { enabled, expectedRevision });
const deletePersonalSkill = (id: string) =>
  assistantRequest<{ deleted: boolean }>(`personal-skills/${encodeURIComponent(id)}`, "DELETE");

export function SkillProposalsCard({ enabled }: { enabled: boolean }) {
  const [proposals, setProposals] = useState<SkillProposal[] | null>(enabled ? null : []);
  const [skills, setSkills] = useState<PersonalSkill[] | null>(enabled ? null : []);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!enabled) return;
    try {
      const [nextProposals, nextSkills] = await Promise.all([
        listSkillProposals(), listPersonalSkills(),
      ]);
      if (signal?.aborted) return;
      setProposals(nextProposals);
      setSkills(nextSkills);
      setError("");
    } catch (cause) {
      if (!signal?.aborted) setError(cause instanceof Error ? cause.message : "个人 Skill 暂时无法读取");
    }
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [enabled, load]);

  async function act(key: string, action: () => Promise<unknown>) {
    setBusy(key);
    setError("");
    try {
      await action();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作未完成，请重试。");
    } finally {
      setBusy(null);
    }
  }

  if (!enabled) return null;
  const active = (proposals ?? []).filter(item => item.status === "proposed" || item.status === "draft");
  const hasNothing = proposals !== null && skills !== null && active.length === 0 && skills.length === 0;

  return <section className={styles.section} aria-label="个人 Skill">
    <h3>个人 Skill</h3>
    {proposals === null || skills === null
      ? <p role="status">正在读取…</p>
      : hasNothing ? <p>重复完成同类任务后，会在这里出现可复用的 Skill 建议。</p> : null}
    {error && <p role="alert" className={styles.error}>{error}</p>}

    {active.map(proposal => {
      const draft = proposal.draft;
      const working = (key: string) => busy === `${proposal.proposalId}:${key}`;
      return <article key={proposal.proposalId} className={styles.card}>
        <strong>{proposal.title}</strong>
        <p>{proposal.summary}</p>
        <small>{proposal.sources.length} 个任务 · 来自你的执行记录</small>
        {proposal.reply && <p className={styles.reply}>{proposal.reply}</p>}
        {proposal.followUpQuestions?.map(question => <small key={question}>· {question}</small>)}
        {draft && <details className={styles.draft}>
          <summary>{draft.name}</summary>
          <p>{draft.description}</p>
          <pre>{draft.instructions}</pre>
        </details>}
        <div className={styles.actions}>
          {!draft && <button type="button" disabled={busy !== null}
            onClick={() => void act(`${proposal.proposalId}:generate`, () => generateSkillProposal(proposal.proposalId))}>
            {working("generate") ? "生成中…" : proposal.reply ? "继续生成" : "生成草稿"}
          </button>}
          {draft && <button type="button" disabled={busy !== null}
            onClick={() => void act(`${proposal.proposalId}:save`, () => saveSkillProposal(proposal.proposalId, proposal.revision))}>
            {working("save") ? "保存中…" : "保存为个人 Skill"}
          </button>}
          <button type="button" className={styles.ghost} disabled={busy !== null}
            onClick={() => void act(`${proposal.proposalId}:dismiss`, () => dismissSkillProposal(proposal.proposalId, proposal.revision))}>
            忽略
          </button>
        </div>
      </article>;
    })}

    {(skills ?? []).map(skill => <article key={skill.skillId} className={styles.skill}>
      <div>
        <strong>{skill.displayName}</strong>
        <p>{skill.description}</p>
      </div>
      <div className={styles.actions}>
        <button type="button" aria-pressed={skill.enabled} disabled={busy !== null}
          onClick={() => void act(`${skill.skillId}:toggle`, () => setPersonalSkillEnabled(skill.skillId, !skill.enabled, skill.revision))}>
          {skill.enabled ? "已启用" : "启用"}
        </button>
        <button type="button" className={styles.ghost} disabled={busy !== null}
          onClick={() => void act(`${skill.skillId}:delete`, () => deletePersonalSkill(skill.skillId))}>
          删除
        </button>
      </div>
    </article>)}
    {(skills ?? []).length > 0 && <small className={styles.note}>保存后默认停用；只有启用的 Skill 会加入新的个人助理任务。</small>}
  </section>;
}
