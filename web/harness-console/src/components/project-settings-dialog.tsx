"use client";

import { useEffect, useId, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { createPortal } from "react-dom";
import {
  projectClient,
  type ApiProject,
  type ApiProjectDocument,
  type ApiProjectMemory,
} from "../lib/studio-client";
import styles from "./project-settings-dialog.module.css";

type Notice = { tone: "error" | "info"; text: string } | null;

function uploadFailure(payload: unknown, status: number): string {
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    const nested = (record.detail ?? record.error) as Record<string, unknown> | undefined;
    if (nested && typeof nested.message === "string") return nested.message;
    if (typeof record.message === "string") return record.message;
  }
  return `上传失败（HTTP ${status}）`;
}

async function uploadProjectDocument(file: File): Promise<string> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/input-artifacts", { method: "POST", body: form });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new Error(uploadFailure(payload, response.status));
  const inputArtifactId =
    payload && typeof payload === "object"
      ? (payload as Record<string, unknown>).input_artifact_id
      : null;
  if (typeof inputArtifactId !== "string" || !inputArtifactId) {
    throw new Error("上传成功但没有返回附件标识，请重试。");
  }
  return inputArtifactId;
}

export function ProjectSettingsDialog({
  project,
  onClose,
  onChanged,
  onRemoved,
}: {
  project: ApiProject;
  onClose: () => void;
  onChanged: (project: ApiProject) => void;
  onRemoved: (projectId: string) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const [name, setName] = useState(project.name);
  const [instructions, setInstructions] = useState(project.instructions ?? "");
  const [memories, setMemories] = useState<ApiProjectMemory[]>([]);
  const [documents, setDocuments] = useState<ApiProjectDocument[]>([]);
  const [memoryDraft, setMemoryDraft] = useState("");
  const [editingMemoryId, setEditingMemoryId] = useState("");
  const [editingMemory, setEditingMemory] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<Notice>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const pending = busy !== "";

  useEffect(() => {
    const dialog = dialogRef.current!;
    dialog.showModal();
    nameRef.current?.focus();
    return () => dialog.close();
  }, []);

  useEffect(() => {
    let active = true;
    projectClient.resources(project.projectId).then((value) => {
      if (!active) return;
      setName(value.project.name);
      setInstructions(value.project.instructions ?? "");
      setMemories(value.memories);
      setDocuments(value.documents);
    }).catch((cause: unknown) => {
      if (active) setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "读取项目失败" });
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [project.projectId]);

  function close() {
    if (!pending) onClose();
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || pending) return;
    setBusy("save");
    setNotice(null);
    try {
      const updated = await projectClient.update(project.projectId, {
        name: trimmed,
        instructions,
      });
      onChanged(updated);
      setNotice({ tone: "info", text: "已保存。新的任务会使用这份项目说明；已有任务的历史不变。" });
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "保存失败，请重试" });
    } finally {
      setBusy("");
    }
  }

  async function addMemory() {
    const content = memoryDraft.trim();
    if (!content || pending) return;
    setBusy("memory");
    setNotice(null);
    try {
      const memory = await projectClient.addMemory(project.projectId, content);
      setMemories((current) => [...current, memory]);
      setMemoryDraft("");
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "添加记忆失败" });
    } finally {
      setBusy("");
    }
  }

  async function saveMemory(memory: ApiProjectMemory) {
    const content = editingMemory.trim();
    if (!content || pending) return;
    setBusy(memory.memoryId);
    setNotice(null);
    try {
      const updated = await projectClient.updateMemory(project.projectId, memory.memoryId, content);
      setMemories((current) => current.map((item) => item.memoryId === updated.memoryId ? updated : item));
      setEditingMemoryId("");
      setEditingMemory("");
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "保存记忆失败" });
    } finally {
      setBusy("");
    }
  }

  async function removeMemory(memory: ApiProjectMemory) {
    if (pending) return;
    setBusy(memory.memoryId);
    setNotice(null);
    try {
      await projectClient.removeMemory(project.projectId, memory.memoryId);
      setMemories((current) => current.filter((item) => item.memoryId !== memory.memoryId));
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "删除记忆失败" });
    } finally {
      setBusy("");
    }
  }

  async function attachFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length || pending) return;
    setBusy("documents");
    setNotice(null);
    try {
      const attached: ApiProjectDocument[] = [];
      for (const file of files) {
        const inputArtifactId = await uploadProjectDocument(file);
        attached.push(await projectClient.attachDocument(project.projectId, inputArtifactId));
      }
      setDocuments((current) => [...current, ...attached]);
      setNotice({ tone: "info", text: `已附加 ${attached.length} 个文档，之后该项目的新任务都会收到它们。` });
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "附加文档失败" });
    } finally {
      setBusy("");
    }
  }

  async function detachDocument(document: ApiProjectDocument) {
    if (pending) return;
    setBusy(document.inputArtifactId);
    setNotice(null);
    try {
      await projectClient.detachDocument(project.projectId, document.inputArtifactId);
      setDocuments((current) => current.filter((item) => item.inputArtifactId !== document.inputArtifactId));
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "移除文档失败" });
    } finally {
      setBusy("");
    }
  }

  async function removeProject() {
    if (pending) return;
    if (!confirmRemove) {
      setConfirmRemove(true);
      return;
    }
    setBusy("remove");
    setNotice(null);
    try {
      await projectClient.remove(project.projectId);
      onRemoved(project.projectId);
      onClose();
    } catch (cause) {
      setNotice({ tone: "error", text: cause instanceof Error ? cause.message : "移除项目失败" });
      setBusy("");
    }
  }

  return createPortal(
    <dialog
      ref={dialogRef}
      className={styles.dialog}
      aria-labelledby={titleId}
      aria-busy={pending}
      onCancel={(event) => { event.preventDefault(); close(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right
          || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
      }}
    >
      <form onSubmit={(event) => void save(event)}>
        <div className={styles.header}>
          <div>
            <h2 id={titleId}>项目设置</h2>
            <p>说明、记忆和文档会随项目带给它的每个新任务。</p>
          </div>
          <button className={styles.close} type="button" aria-label="关闭项目设置" disabled={pending} onClick={close}>
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
          </button>
        </div>

        <label className={styles.field}>
          <span>项目名称</span>
          <input
            ref={nameRef}
            value={name}
            maxLength={120}
            required
            disabled={pending || loading}
            onChange={(event) => { setName(event.target.value); setNotice(null); }}
          />
        </label>

        <label className={styles.field}>
          <span>项目说明</span>
          <textarea
            value={instructions}
            rows={5}
            maxLength={20000}
            disabled={pending || loading}
            placeholder="例如：目标、术语、输出规范、不可违反的约束。"
            onChange={(event) => { setInstructions(event.target.value); setNotice(null); }}
          />
          <small>会作为项目上下文附加到该项目下所有任务。</small>
        </label>

        <section className={styles.section} aria-label="项目记忆">
          <div className={styles.sectionHead}>
            <div>
              <h3>项目记忆</h3>
              <p>长期有效的事实和决定。不是每轮对话的临时上下文。</p>
            </div>
            <span className={styles.count}>{memories.length}</span>
          </div>
          {loading ? <p className={styles.empty}>正在读取…</p> : (
            <ul className={styles.list}>
              {memories.map((memory) => (
                <li key={memory.memoryId} className={styles.item}>
                  {editingMemoryId === memory.memoryId ? (
                    <>
                      <textarea
                        value={editingMemory}
                        rows={3}
                        maxLength={4000}
                        aria-label="编辑项目记忆"
                        onChange={(event) => setEditingMemory(event.target.value)}
                      />
                      <div className={styles.itemActions}>
                        <button type="button" disabled={pending} onClick={() => { setEditingMemoryId(""); setEditingMemory(""); }}>取消</button>
                        <button type="button" className={styles.primarySmall} disabled={pending || !editingMemory.trim()} onClick={() => void saveMemory(memory)}>保存</button>
                      </div>
                    </>
                  ) : (
                    <>
                      <p>{memory.content}</p>
                      <div className={styles.itemActions}>
                        <button type="button" disabled={pending} onClick={() => { setEditingMemoryId(memory.memoryId); setEditingMemory(memory.content); }}>编辑</button>
                        <button type="button" className={styles.dangerSmall} disabled={pending} onClick={() => void removeMemory(memory)}>删除</button>
                      </div>
                    </>
                  )}
                </li>
              ))}
              {!memories.length && <li className={styles.empty}>还没有项目记忆。</li>}
            </ul>
          )}
          <div className={styles.composer}>
            <textarea
              value={memoryDraft}
              rows={2}
              maxLength={4000}
              placeholder="添加一条项目记忆…"
              aria-label="新的项目记忆"
              disabled={pending || loading}
              onChange={(event) => setMemoryDraft(event.target.value)}
            />
            <button type="button" disabled={pending || loading || !memoryDraft.trim()} onClick={() => void addMemory()}>
              添加记忆
            </button>
          </div>
        </section>

        <section className={styles.section} aria-label="项目文档">
          <div className={styles.sectionHead}>
            <div>
              <h3>项目文档</h3>
              <p>附加的文件会出现在该项目每个新任务的输入附件里。</p>
            </div>
            <span className={styles.count}>{documents.length}</span>
          </div>
          {loading ? <p className={styles.empty}>正在读取…</p> : (
            <ul className={styles.list}>
              {documents.map((document) => (
                <li key={document.inputArtifactId} className={styles.document}>
                  <span className={styles.documentIcon} aria-hidden="true">
                    <svg viewBox="0 0 20 20"><path d="M5 2.75h6l4 4v10.5H5Z" /><path d="M11 2.75v4h4" /></svg>
                  </span>
                  <span className={styles.documentName} title={document.name}>{document.name}</span>
                  <span className={styles.documentSize}>{Math.max(1, Math.round(document.sizeBytes / 1024))} KB</span>
                  <button type="button" className={styles.dangerSmall} disabled={pending} onClick={() => void detachDocument(document)}>移除</button>
                </li>
              ))}
              {!documents.length && <li className={styles.empty}>还没有项目文档。</li>}
            </ul>
          )}
          <label className={styles.upload}>
            <input type="file" multiple disabled={pending || loading || documents.length >= 10} onChange={(event) => void attachFiles(event)} />
            <span>{busy === "documents" ? "正在上传…" : "添加文档"}</span>
          </label>
          <small>最多 10 个附件，与单次任务共用附件大小限制。</small>
        </section>

        {notice && <p className={styles.notice} data-tone={notice.tone} role={notice.tone === "error" ? "alert" : "status"}>{notice.text}</p>}

        <div className={styles.footer}>
          <div className={styles.removeGroup}>
            <button type="button" className={styles.remove} disabled={pending} onClick={() => void removeProject()}>
              {confirmRemove ? "确认移除项目" : "移除项目"}
            </button>
            {confirmRemove && <small>任务会回到「任务」区，历史不会删除。</small>}
          </div>
          <div className={styles.footerActions}>
            <button type="button" className={styles.cancel} disabled={pending} onClick={close}>取消</button>
            <button type="submit" className={styles.submit} disabled={pending || loading || !name.trim()}>
              {busy === "save" ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      </form>
    </dialog>,
    document.body,
  );
}
