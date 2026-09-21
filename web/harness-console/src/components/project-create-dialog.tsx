"use client";

import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import { projectClient, type ApiProject } from "../lib/studio-client";
import styles from "./project-create-dialog.module.css";

export function ProjectCreateDialog({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (project: ApiProject) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const pendingRef = useRef(false);
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const titleId = useId();
  const errorId = useId();

  useEffect(() => {
    const dialog = dialogRef.current!;
    dialog.showModal();
    inputRef.current?.focus();
    return () => dialog.close();
  }, []);

  function close() {
    if (!pendingRef.current) onClose();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName || pendingRef.current) return;
    pendingRef.current = true;
    setPending(true);
    setError("");
    try {
      const project = await projectClient.create(trimmedName);
      onCreated(project);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建项目失败，请重试");
    } finally {
      pendingRef.current = false;
      setPending(false);
    }
  }

  return createPortal(
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); close(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
      }}
    >
      <form onSubmit={(event) => void submit(event)} aria-busy={pending}>
        <div className={styles.header}>
          <h2 id={titleId}>创建项目</h2>
          <button className={styles.close} type="button" aria-label="关闭创建项目" disabled={pending} onClick={close}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
          </button>
        </div>
        <label className={styles.name}>
          <span className={styles.folder} aria-hidden="true">
            <svg viewBox="0 0 20 20"><path d="M3 6a2 2 0 0 1 2-2h3l2 2h5a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Zm0 3h14" /></svg>
          </span>
          <input ref={inputRef} aria-label="项目名称" placeholder="项目名称" value={name}
            maxLength={120} required disabled={pending} autoComplete="off"
            aria-describedby={error ? errorId : undefined}
            onChange={(event) => { setName(event.target.value); setError(""); }} />
        </label>
        {error && <p id={errorId} className={styles.error} role="alert">{error}</p>}
        <div className={styles.footer}>
          <button type="button" className={styles.cancel} disabled={pending} onClick={close}>取消</button>
          <button type="submit" className={styles.submit} disabled={!name.trim() || pending}>
            {pending ? "创建中…" : "创建项目"}
          </button>
        </div>
      </form>
    </dialog>, document.body,
  );
}
