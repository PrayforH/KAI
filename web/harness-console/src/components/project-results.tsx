"use client";
import Link from "next/link";
import { useState } from "react";
import { projectClient, type ApiProjectResult } from "../lib/studio-client";
import type { ProjectSpaceState } from "./use-project-space";
import { errorText, projectEmpty, size } from "./project-space-shared";
import { formatTaskAge } from "../lib/task-list-age";
import { SaveProjectResultButton } from "./save-project-result-button";
import styles from "./project-home.module.css";

export function ProjectResults({ space }: { space: ProjectSpaceState }) {
  const {
    project,
    artifacts,
    resultError,
    savedResultError,
    savedResults,
    setSavedResults,
    query,
    setQuery,
    refreshResources,
  } = space;
  const [showOutputs, setShowOutputs] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  if (!project) return null;
  const visible = savedResults.filter((item) =>
    [item.name, item.threadTitle].some((value) =>
      value.toLowerCase().includes(query.toLowerCase()),
    ),
  );
  async function act(result: ApiProjectResult, action: "finalize" | "collect") {
    if (pending || !project) return;
    setPending(result.resultId);
    setError("");
    setNotice("");
    try {
      if (action === "finalize") {
        const updated = await projectClient.finalizeResult(
          project.projectId,
          result.resultId,
          !result.finalizedAt,
        );
        setSavedResults((current) =>
          current.map((item) =>
            item.resultId === updated.resultId ? updated : item,
          ),
        );
      } else {
        await projectClient.attachDocument(
          project.projectId,
          result.inputArtifactId,
        );
        await refreshResources();
        setNotice(`已将「${result.name}」加入项目资料。`);
      }
    } catch (cause) {
      setError(errorText(cause));
    } finally {
      setPending(null);
    }
  }
  return (
    <section className={styles.section}>
      <p className={styles.muted}>
        已保存的文件快照独立保留。同名文件保存为新版本；定稿后仍可追溯来源任务。
      </p>
      <div className={styles.toolbar}>
        <input
          className={styles.input}
          aria-label="搜索项目成果"
          placeholder="搜索成果或来源任务…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button
          className={styles.button}
          aria-expanded={showOutputs}
          onClick={() => setShowOutputs((current) => !current)}
        >
          {showOutputs ? "收起任务产物" : "从任务产物中保存"}
        </button>
      </div>
      {(error || savedResultError) && (
        <p className={styles.error} role="alert">
          {error || savedResultError}
        </p>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}
        </p>
      )}
      {visible.length ? (
        <ul className={styles.list}>
          {visible.map((result) => (
            <li className={styles.resultRow} key={result.resultId}>
              <div className={styles.copy}>
                <a
                  className={styles.rowLink}
                  href={`/api/input-artifacts/${encodeURIComponent(result.inputArtifactId)}/content?inline=1`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <strong>{result.name}</strong>
                </a>
                <small>
                  v{result.version} · {result.finalizedAt ? "已定稿" : "已保存"}{" "}
                  · {size(result.sizeBytes)} · {formatTaskAge(result.createdAt)}
                </small>
                <Link
                  className={styles.sourceLink}
                  href={`/?thread=${encodeURIComponent(result.threadId)}`}
                >
                  来源：{result.threadTitle} ↗
                </Link>
              </div>
              <div className={styles.resultActions}>
                <a
                  className={styles.textLink}
                  href={`/api/input-artifacts/${encodeURIComponent(result.inputArtifactId)}/content?inline=1`}
                  target="_blank"
                  rel="noreferrer"
                >
                  预览
                </a>
                <a
                  className={styles.textLink}
                  href={`/api/input-artifacts/${encodeURIComponent(result.inputArtifactId)}/content`}
                  download={result.name}
                >
                  下载
                </a>
                <button
                  className={styles.button}
                  disabled={Boolean(pending)}
                  onClick={() => void act(result, "finalize")}
                >
                  {result.finalizedAt ? "取消定稿" : "标记定稿"}
                </button>
                <button
                  className={styles.button}
                  disabled={
                    Boolean(pending) ||
                    space.resources?.documents.some(
                      (item) => item.inputArtifactId === result.inputArtifactId,
                    )
                  }
                  onClick={() => void act(result, "collect")}
                >
                  {space.resources?.documents.some(
                    (item) => item.inputArtifactId === result.inputArtifactId,
                  )
                    ? "已加入资料"
                    : "加入项目资料"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        !savedResultError &&
        projectEmpty(
          query ? "没有匹配的成果" : "还没有保存的项目成果",
        )
      )}
      {showOutputs && (
        <section className={styles.outputSection}>
          <h2>任务产物</h2>
          <p className={styles.muted}>
            包含归档任务。保存后才进入项目成果，加入项目资料后才供后续任务使用。
          </p>
          {resultError && (
            <p role="alert" className={styles.error}>
              {resultError}
            </p>
          )}
          <ul className={styles.list}>
            {artifacts
              .filter((item) =>
                [item.name, item.thread_title].some((value) =>
                  value.toLowerCase().includes(query.toLowerCase()),
                ),
              )
              .map((artifact) => (
                <li className={styles.row} key={artifact.artifact_id}>
                  <Link
                    className={styles.rowLink}
                    href={`/?thread=${encodeURIComponent(artifact.thread_id)}`}
                  >
                    <strong>{artifact.name}</strong>
                    <small>
                      {artifact.thread_title}
                      {artifact.task_archived ? " · 任务已归档" : ""}
                    </small>
                  </Link>
                  <SaveProjectResultButton
                    projectId={project.projectId}
                    artifactId={artifact.artifact_id}
                    initiallySaved={savedResults.some(
                      (item) => item.artifactId === artifact.artifact_id,
                    )}
                    onSaved={(result) =>
                      setSavedResults((current) =>
                        current.some(
                          (item) => item.resultId === result.resultId,
                        )
                          ? current
                          : [result, ...current],
                      )
                    }
                  />
                </li>
              ))}
          </ul>
          {!artifacts.length && !resultError && (
            <p className={styles.muted}>项目任务尚未生成文件。</p>
          )}
        </section>
      )}
    </section>
  );
}
