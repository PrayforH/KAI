"use client";
import { WorkspaceAttachments } from "./workspace-attachments";
import { useEffect, useRef, useState } from "react";
import { createInputAttachmentAdapter, inputArtifactIdFromAttachment } from "../../lib/input-attachment-adapter";
import { PreviewRunResponse, type PreviewTurn } from "./agent-preview";
import styles from "./build-workspace.module.css";

export function AgentTestPanel({ incomingFiles, onIncomingFilesUsed, turns, draftId, revision, agentName, model, busy, ready, dirty, error, selectedRunId, onSend, onReset, onCancel, onImprove, onAssets }: {
  onIncomingFilesUsed?: () => void;
  incomingFiles?: {draftId: string; files: {id: string; name: string}[]} | null;
  turns: PreviewTurn[]; draftId: string; revision: number; agentName: string; model: string; busy: boolean; ready: boolean; dirty: boolean; error: string; selectedRunId: string;
  onSend: (value: string, ids: string[], names: string[]) => Promise<boolean>; onReset: () => void; onCancel: () => Promise<void>; onImprove: (turn: PreviewTurn) => void; onAssets: () => void;
}) {
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<{id: string; name: string; mediaType?: string}[]>([]);
  const [uploading, setUploading] = useState(false);
  const [sending, setSending] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const epoch = useRef(0);
  const uploadLock = useRef(false);
  const incomingUsed = useRef<typeof incomingFiles>(null);
  const composing = useRef(false);
  const compositionEnded = useRef(-Infinity);
  const transcript = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const last = turns.at(-1)?.result;
  useEffect(() => { epoch.current++; uploadLock.current = false; setInput(""); setFiles([]); setUploadError(""); setUploading(false); setSending(false); follow.current = true; }, [draftId]);
  useEffect(() => { if (incomingFiles && incomingFiles !== incomingUsed.current && incomingFiles.draftId === draftId) { incomingUsed.current = incomingFiles; setFiles(current => [...current, ...incomingFiles.files.filter(file => !current.some(item => item.id === file.id))]); onIncomingFilesUsed?.(); } }, [incomingFiles, draftId, onIncomingFilesUsed]);
  useEffect(() => { if (follow.current) transcript.current?.scrollTo({top: transcript.current.scrollHeight, behavior: "smooth"}); }, [turns.length, last?.events.length, last?.finalText]);
  useEffect(() => {
    const root = transcript.current;
    const turn = Array.from(root?.querySelectorAll<HTMLElement>("[data-test-run]") ?? []).find(node => node.dataset.testRun === selectedRunId);
    if (root && turn) { follow.current = false; root.scrollTo({top: turn.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop - 16, behavior: "smooth"}); }
  }, [selectedRunId]);
  async function send() {
    if (!ready || busy || sending || uploading || !input.trim()) return;
    const current = epoch.current; setSending(true); follow.current = true;
    try { if (await onSend(input, files.map(f => f.id), files.map(f => f.name)) && current === epoch.current) { setInput(""); setFiles([]); } }
    finally { if (current === epoch.current) setSending(false); }
  }
  async function upload(selected: File[]) {
    if (!ready || busy || sending || uploadLock.current || !selected.length) return;
    uploadLock.current = true;
    const current = epoch.current; setUploading(true); setUploadError("");
    try {
      const adapter = createInputAttachmentAdapter();
      for (const file of selected) {
        const iterator = adapter.add({file});
        if (!(Symbol.asyncIterator in iterator)) throw new Error("附件上传不可用");
        for await (const pending of iterator) {
          if (pending.status.type !== "requires-action") continue;
          const attachment = await adapter.send(pending); const id = inputArtifactIdFromAttachment(attachment);
          if (id && current === epoch.current) { setFiles(list => [...list, {id, name: file.name, mediaType: attachment.contentType}]); }
        }
      }
    } catch (reason) { if (current === epoch.current) setUploadError(reason instanceof Error ? reason.message : "上传失败"); }
    finally { if (current === epoch.current) { uploadLock.current = false; setUploading(false); } }
  }
  return <section className={styles.testPanel} aria-label="智能体效果测试">
    <header className={styles.panelHeader}><div><strong>效果测试</strong><small title={model}>{model || "使用智能体配置模型"}</small></div><div className={styles.headerActions}><button type="button" onClick={onAssets}>配置与文件</button><button type="button" disabled={busy || sending} onClick={onReset}>新对话</button></div></header>
    <div className={styles.revisionNote} role="status">{!ready ? "先在左侧描述需求，创建智能体后即可测试" : dirty ? "有未保存修改，发送测试时将先保存配置" : last && last.draftRevision !== revision ? `配置已更新至 r${revision} · 下一次测试将开启新会话` : `草稿 r${revision} · 连续对话`}</div>
    <div className={styles.testTranscript} ref={transcript} onScroll={event => {const el=event.currentTarget;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<120;}}>
      {!turns.length && <div className={styles.emptyTest}><span aria-hidden="true">↗</span><h2>{ready ? agentName : "从一个需求开始"}</h2><p>{ready ? "输入实际问题或附加材料，查看智能体的回答效果。" : "左侧负责构建和修改，这里用于验证效果。"}</p></div>}
      {turns.map((turn, index) => <article key={turn.result.run.run_id} data-test-run={turn.result.run.run_id} className={styles.testTurn} data-selected={turn.result.run.run_id === selectedRunId}>
        {index > 0 && turns[index-1].result.run.session_id !== turn.result.run.session_id && <div className={styles.sessionBoundary}>新会话 · r{turn.result.draftRevision}</div>}
        <div className={styles.testUser}><p>{turn.prompt}</p>{turn.files?.length ? <WorkspaceAttachments files={turn.files.map((name,i)=>({id:turn.artifactIds?.[i] || `legacy-${i}`,name}))}/> : null}</div>
        <div className={styles.turnRevision}>r{turn.result.draftRevision}{turn.result.draftRevision !== revision ? " · 历史配置" : ""}</div>
        <PreviewRunResponse turn={turn} agentName={agentName} onImprove={onImprove}/>
      </article>)}
      {busy && !last && <p className={styles.revisionNote}>正在准备测试…</p>}
    </div>
    {(error || uploadError) && <p className={styles.testError} role="alert">{error || uploadError}</p>}
    <footer data-test-composer className={`${styles.testComposer} harness-composer-shell`} onPaste={event => { const pasted = Array.from(event.clipboardData.files); if (pasted.length) { event.preventDefault(); void upload(pasted); } }} onDragOver={event => { if (Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault(); }} onDrop={event => { const dropped = Array.from(event.dataTransfer.files); if (dropped.length) { event.preventDefault(); void upload(dropped); } }}><div className="aui-composer-root">
      {files.length > 0 && <WorkspaceAttachments files={files} disabled={sending} onRemove={id=>setFiles(list=>list.filter(file=>file.id!==id))}/> }
      <textarea className="aui-composer-input" aria-label="效果测试输入" placeholder={ready ? "输入问题或附加材料，测试智能体…" : "创建智能体后，在这里测试…"} disabled={!ready || sending} rows={2} value={input} maxLength={100000} onChange={event=>setInput(event.target.value)} onCompositionStart={()=>{composing.current=true;}} onCompositionEnd={()=>{composing.current=false;compositionEnded.current=Date.now();}} onKeyDown={event=>{if(composing.current||event.nativeEvent.isComposing||event.keyCode===229)return;if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();if(Date.now()-compositionEnded.current>=80)void send();}}}/>
      <div className="composer-toolbar"><label className={`${styles.attachment} aui-composer-attach`}>＋<input type="file" aria-label="添加测试附件" multiple disabled={!ready||busy||uploading||sending} onChange={event=>{void upload(Array.from(event.target.files??[]));event.target.value="";}}/></label>{uploading && <span className="composer-status-announcement" role="status">上传中…</span>}{busy ? <button type="button" className="aui-button aui-button-icon aui-composer-cancel" aria-label="停止测试" onClick={()=>void onCancel()}>■</button> : <button type="button" className="aui-button aui-button-icon aui-composer-send" aria-label="发送测试消息" disabled={!ready||!input.trim()||uploading||sending} onClick={()=>void send()}><svg className="aui-composer-send-icon" viewBox="0 0 20 20"><path d="M10 15V5m-5 5 5-5 5 5"/></svg></button>}</div>
    </div></footer>
  </section>;
}
