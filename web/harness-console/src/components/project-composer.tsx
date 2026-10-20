"use client";

import { useEffect, useRef, useState } from "react";
import type { ProjectSpaceState } from "./use-project-space";
import { ConversationDraftInput } from "./conversation-input";
import { ConversationControl } from "./conversation-control";
import { TaskAgentSwitcher } from "./task-agent-switcher";
import { TaskModelControl, TaskModelProvider } from "./task-model-context";
import { loadTaskModelRoutes, type TaskModelRoute } from "../lib/task-model-catalog";
import { VoiceInput } from "./voice-input";
import { useDictationComposer } from "./use-dictation-composer";
import styles from "./project-home.module.css";

export function ProjectComposer({ space }: { space: ProjectSpaceState }) {
  const { project, draft, setDraft, creating, selectedAgent, agents, setSelectedAgent, startTask, setDialog } = space;
  const input = useRef<HTMLTextAreaElement>(null);
  const composing = useRef(false);
  const [voiceActive, setVoiceActive] = useState(false);
  const [routes, setRoutes] = useState<TaskModelRoute[]>([]);
  const [model, setModel] = useState<string | null>(null);
  const dictation = useDictationComposer(input, () => draft, setDraft, composing);
  useEffect(() => {
    let active = true;
    loadTaskModelRoutes().then(items => {
      // Video generation uses its own main-conversation workflow.
      if (active) setRoutes(items.filter(item => item.modelType !== "video_generation"));
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);
  if (!project) return null;
  const locked = creating || Boolean(project.archivedAt);
  const canSend = !locked && !voiceActive && Boolean(selectedAgent) && Boolean(draft.trim());
  return <div className={`${styles.projectComposer} harness-composer-shell`} data-project-launch-composer>
    <TaskModelProvider routes={routes} agentDefaultRouteId={selectedAgent?.modelRoute ?? null}
      overrideRouteId={model} onOverrideChange={setModel}>
      <form className="aui-composer-root" aria-label="在项目中开始任务"
        onSubmit={event => {
          event.preventDefault();
          if (canSend && !composing.current) void startTask(draft, model);
        }}>
        <div className="composer-input-wrap">
          <ConversationDraftInput ref={input} className="aui-composer-input" aria-label="项目任务需求"
            placeholder={project.archivedAt ? "项目已归档，恢复后可继续任务" : "在此项目中启动任务…"}
            value={draft} maxLength={100000} disabled={locked}
            rows={Math.min(8, Math.max(2, draft.split("\n").length))}
            onComposingChange={value => { composing.current = value; }}
            onCompositionEnd={() => dictation.onCompositionEnd()}
            onChange={event => { dictation.onChange(event.target.value); setDraft(event.target.value); }}
            onKeyDown={event => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (canSend) event.currentTarget.form?.requestSubmit();
              }
            }} />
        </div>
        <div className="composer-footer">
          <div className="composer-toolbar">
            <button type="button" className="aui-composer-attach" aria-label="添加项目文件和资源" title="添加项目文件和资源"
              disabled={locked || voiceActive} onClick={() => setDialog("files")}>
              <svg className="aui-composer-attach-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 4.5v11M4.5 10h11" /></svg>
            </button>
            <button type="button" className="aui-composer-attach" aria-label="关联项目知识库" title="关联项目知识库"
              disabled={locked || voiceActive} onClick={() => setDialog("knowledge")}>@</button>
            <TaskAgentSwitcher agents={agents} selected={selectedAgent} loading={locked || voiceActive}
              currentTaskBusy={locked || voiceActive} menuPlacement="above" onChange={setSelectedAgent} />
            <div className="composer-model-voice">
              <TaskModelControl disabled={locked || voiceActive} />
              <VoiceInput disabled={locked} modelRoute={model ?? ""}
                onActive={active => { dictation.onActive(active); setVoiceActive(active); }} onInsert={dictation.onRecorded} />
            </div>
          </div>
          <ConversationControl action="send" type="submit" aria-label="发送并开始项目任务" disabled={!canSend} />
        </div>
      </form>
    </TaskModelProvider>
  </div>;
}
