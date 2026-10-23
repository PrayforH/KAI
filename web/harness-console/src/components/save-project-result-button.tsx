"use client";
import { useRef, useState } from "react";
import { projectClient, type ApiProjectResult } from "../lib/studio-client";
import styles from "./project-home.module.css";

export function SaveProjectResultButton({
  projectId,
  artifactId,
  initiallySaved = false,
  onSaved,
}: {
  projectId: string;
  artifactId: string;
  initiallySaved?: boolean;
  onSaved?: (result: ApiProjectResult) => void;
}) {
  const saving = useRef(false);
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  return (
    <span className={styles.saveResultControl}>
      <button
        type="button"
        className={styles.button}
        disabled={pending || saved || initiallySaved}
        onClick={async () => {
          if (saving.current || pending || saved || initiallySaved) return;
          saving.current = true;
          setPending(true);
          setError("");
          try {
            const result = await projectClient.saveResult(
              projectId,
              artifactId,
            );
            setSaved(true);
            onSaved?.(result);
            window.dispatchEvent(
              new CustomEvent("harness:project-results-changed", {
                detail: { projectId },
              }),
            );
          } catch (cause) {
            setError(
              cause instanceof Error ? cause.message : "保存成果失败，请重试。",
            );
          } finally {
            saving.current = false;
            setPending(false);
          }
        }}
      >
        {pending
          ? "保存中…"
          : saved || initiallySaved
            ? "已保存到项目成果"
            : "保存到项目成果"}
      </button>
      {error && (
        <span className={styles.saveResultError} role="alert">
          {error}
        </span>
      )}
    </span>
  );
}
