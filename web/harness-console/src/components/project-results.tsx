"use client";
import Link from "next/link";
import type { ProjectSpaceState } from "./use-project-space";
import { projectEmpty, size } from "./project-space-shared";
import { formatTaskAge } from "../lib/task-list-age";
import styles from "./project-home.module.css";

export function ProjectResults({ space }: { space: ProjectSpaceState }) {
  const {
    artifacts,
    resultError,
    query,
    setQuery,
    importing,
    imported,
    collect,
    visibleArtifacts,
  } = space;
  return (
    <section className={styles.section}>
      <div className={styles.sectionHead}>
        <h2>项目成果</h2>
        <span className={styles.count}>
          {artifacts.length === 500
            ? "显示最近 500 项"
            : `${artifacts.length} 项成果`}
        </span>
      </div>
      <p className={styles.muted}>
        汇总项目内各任务的产物，包含归档任务。加入项目资料后，后续任务才能复用该文件。
      </p>
      <div className={styles.toolbar}>
        <input
          className={styles.input}
          aria-label="搜索项目成果"
          placeholder="搜索文件或来源任务…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {resultError && (
        <p className={styles.error} role="alert">
          {resultError}
        </p>
      )}
      {visibleArtifacts.length ? (
        <ul className={styles.list}>
          {visibleArtifacts.map((artifact) => (
            <li className={styles.row} key={artifact.artifact_id}>
              <Link
                className={styles.rowLink}
                href={`/?thread=${encodeURIComponent(artifact.thread_id)}`}
              >
                <strong>{artifact.name}</strong>
                <small>
                  {artifact.thread_title}
                  {artifact.task_archived ? " · 任务已归档" : ""} ·{" "}
                  {formatTaskAge(artifact.created_at)} ·{" "}
                  {size(artifact.size_bytes)}
                </small>
              </Link>
              <a
                className={styles.textLink}
                href={`/api/harness/artifacts/${encodeURIComponent(artifact.artifact_id)}`}
                target="_blank"
                rel="noreferrer"
              >
                下载
              </a>
              <button
                className={styles.button}
                disabled={
                  Boolean(importing) || imported.has(artifact.artifact_id)
                }
                onClick={() => void collect(artifact)}
              >
                {imported.has(artifact.artifact_id)
                  ? "已加入资料"
                  : importing === artifact.artifact_id
                    ? "加入中…"
                    : "加入项目资料"}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        projectEmpty(
          query ? "没有匹配的成果" : "还没有交付成果",
          "在项目中完成文件生成任务后，成果会按来源任务汇总在这里。",
        )
      )}
    </section>
  );
}
