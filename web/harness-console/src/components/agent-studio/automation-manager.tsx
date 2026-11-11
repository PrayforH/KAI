"use client";
import { FeedbackToast } from "../feedback-toast";
import { DutyScheduleControl, type BotDuty } from "../duty-schedule-control";
import { assistantRequest, type Execution } from "../../lib/assistant-client";
import { taskThreadHref } from "../../lib/conversation-navigation";
import {
  filterRunRecords,
  mergeRunRecords,
  type DutyRun,
  type UnifiedRecord,
  type UnifiedRecordStatus,
} from "../../lib/automation-records";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRandomId } from "../../lib/random-id";
import styles from "./automation-manager.module.css";
import { ScheduleCardActions, type ScheduleCardActionKind } from "./schedule-card-actions";
import {
  automationClient,
  type ApiAutomationRunRecord,
  type ApiAutomationTask,
  type ApiSchedule,
  type AutomationPermissionKind,
  type AutomationTaskInput,
} from "../../lib/studio-client";
import { MODEL_ROUTES } from "../../lib/agent-studio";
import {
  WEEKDAY_LABELS,
  buildCron,
  formatDuration,
  formatScheduleTime,
  localDatetimeValue,
  localTimezone,
  nextWeekdayEvenHour,
  readCronFrequency,
  scheduleSummary,
  type ScheduleFrequency,
} from "../../lib/automation-schedule";

type Tab = "tasks" | "records";
type FrequencyType = ScheduleFrequency;

interface TemplateDef {
  id: string;
  icon: string;
  name: string;
  description: string;
  frequency: FrequencyType;
  weekday?: number;
  time?: string;
  cron?: string;
}

const TEMPLATES: TemplateDef[] = [
  { id: "ai-news", icon: "📰", name: "每日 AI 新闻推送", description: "关注当天 AI 领域的重要动态，侧重 AI coding 与具身智能…", frequency: "daily", time: "08:00" },
  { id: "english-words", icon: "🔤", name: "每日 5 个英语单词", description: "每天推荐 5 个高频实用英语单词，包含词义、音标、例句…", frequency: "daily", time: "08:30" },
  { id: "bedtime-story", icon: "🌙", name: "每日儿童睡前故事", description: "生成 3-5 分钟可读的温和睡前故事，情节完整并附简短…", frequency: "daily", time: "20:30" },
  { id: "weekly-report", icon: "📋", name: "每周工作周报", description: "每周五汇总仓库 PR 与 Issue 进展，输出关键变更与待办…", frequency: "weekly", weekday: 5, time: "18:00" },
  { id: "movie", icon: "🎬", name: "经典电影推荐", description: "推荐一部高分经典电影，简要介绍剧情梗概、亮点与推荐理由…", frequency: "daily", time: "19:00" },
  { id: "history-today", icon: "📅", name: "历史上的今天", description: "从科技、电影、音乐等领域挑选一件\u201c今天发生过\u201d的有趣事…", frequency: "daily", time: "09:00" },
  { id: "why", icon: "💡", name: "每日一个为什么", description: "每天抛出一个有趣问题，先提问再解答，语气轻松、通俗易懂…", frequency: "daily", time: "12:00" },
  { id: "parents", icon: "📞", name: "父母联系提醒", description: "每周日 10:00 提醒你给家人打电话或发消息，简单问候…", frequency: "weekly", weekday: 0, time: "10:00" },
  { id: "checkup", icon: "🏥", name: "体检预约提醒", description: "在指定时间提醒你确认体检时间、准备证件…", frequency: "once", time: "07:00" },
  { id: "interview", icon: "💬", name: "面试准备提醒", description: "工作日每 2 小时提醒你复习大模型面试内容，并生成 3…", frequency: "weekdays" },
  { id: "meeting", icon: "🗂️", name: "会议前准备", description: "在会议开始前提醒你整理议题、目标、待确认问题和关键…", frequency: "once", time: "09:00" },
  { id: "wallpaper", icon: "🐱", name: "可爱萌宠手机壁纸", description: "随机从 7 种不同风格中挑选一种，为你生成一张 9:16 壁纸…", frequency: "daily", time: "09:30" },
];

const RECORD_STATUS_LABELS: Record<UnifiedRecordStatus, string> = {
  running: "运行中",
  success: "成功",
  failed: "失败",
  cancelled: "已取消",
};

const DUTY_STATUS_LABELS: Record<string, string> = {
  active: "已启用",
  paused: "已暂停",
  completed: "已结束",
  cancelled: "已结束",
};

/** A schedule that can no longer act: objective completed/cancelled, or an
 *  automation that ran out of validity. The default list hides these so a
 *  delete visibly removes the card; records stay readable in 运行记录. */
const TERMINAL_STATUSES = new Set(["completed", "cancelled", "expired"]);

function isTerminalStatus(status: string): boolean {
  return TERMINAL_STATUSES.has(status);
}

function dutyKey(duty: ApiSchedule): string {
  return `${duty.source}:${duty.sourceId}`;
}

function taskKey(task: ApiAutomationTask): string {
  return `automation:${task.taskId}`;
}

/** A schedule row as the duty editor expects it; the seconds→minutes split is
 *  the one shape difference between the list payload and the editor. */
function botDutyFromSchedule(duty: ApiSchedule): BotDuty {
  const { seconds, ...timing } = duty.timing;
  return {
    objectiveId: duty.sourceId,
    title: duty.title,
    status: duty.status,
    revision: duty.revision,
    nextCheckAt: duty.nextRunAt,
    schedule: { ...timing, intervalMinutes: seconds == null ? null : seconds / 60 },
  };
}

function defaultOnceAt(): string {
  const soon = new Date(Date.now() + 10 * 60 * 1000);
  soon.setSeconds(0, 0);
  return localDatetimeValue(soon);
}

interface FormState {
  name: string;
  prompt: string;
  model: string;
  workspaceId: string;
  permission: AutomationPermissionKind;
  frequency: FrequencyType;
  at: string;
  time: string;
  weekday: number;
  cron: string;
  validity: "forever" | "until";
  until: string;
}

function emptyForm(): FormState {
  return {
    name: "",
    prompt: "",
    model: "auto",
    workspaceId: "",
    permission: "restricted",
    frequency: "once",
    at: defaultOnceAt(),
    time: "08:00",
    weekday: 5,
    cron: "0 9 * * *",
    validity: "forever",
    until: "",
  };
}

function formFromTemplate(template: TemplateDef): FormState {
  const base = emptyForm();
  const at = new Date(Date.now() + 24 * 60 * 60 * 1000);
  at.setSeconds(0, 0);
  const [hour, minute] = (template.time ?? "08:00").split(":").map(Number);
  at.setHours(hour || 0, minute || 0);
  return {
    ...base,
    name: template.name,
    prompt: `请帮我完成：${template.description.replace(/…$/, "")}`,
    frequency: template.frequency,
    at: template.frequency === "once" ? localDatetimeValue(at) : base.at,
    time: template.time ?? "08:00",
    weekday: template.weekday ?? 5,
    cron: template.cron ?? base.cron,
  };
}

function formFromTask(task: ApiAutomationTask): FormState {
  const base = emptyForm();
  let frequency: FrequencyType = "custom";
  let at = base.at;
  let time = base.time;
  let weekday = base.weekday;
  const cron = task.schedule.cron ?? base.cron;
  if (task.schedule.type === "once") {
    frequency = "once";
    at = task.schedule.at ? localDatetimeValue(new Date(task.schedule.at)) : base.at;
  } else {
    // Only a cron that is exactly what a frequency produces may be shown as that
    // frequency; anything else keeps its expression, so opening and saving a task
    // cannot silently change when it runs.
    const reading = readCronFrequency(cron, weekday);
    if (reading) {
      frequency = reading.frequency;
      if ("time" in reading) time = reading.time;
      if ("weekday" in reading) weekday = reading.weekday;
    }
  }
  return {
    ...base,
    name: task.name,
    prompt: task.prompt,
    model: task.model || "auto",
    workspaceId: task.workspaceId ?? "",
    permission: task.permission,
    frequency,
    at,
    time,
    weekday,
    cron,
    validity: task.validity.type,
    until: task.validity.until ? task.validity.until.slice(0, 10) : "",
  };
}

function formToInput(form: FormState): AutomationTaskInput {
  const timezone = localTimezone();
  const schedule: AutomationTaskInput["schedule"] =
    form.frequency === "once"
      ? { type: "once", at: new Date(form.at).toISOString(), timezone }
      : {
          type: "cron",
          cron: form.frequency === "custom" ? form.cron : buildCron(form.frequency, form.time, form.weekday),
          timezone,
        };
  return {
    name: form.name.trim(),
    prompt: form.prompt.trim(),
    model: form.model || "auto",
    workspaceId: form.workspaceId || null,
    permission: form.permission,
    schedule,
    validity:
      form.validity === "until" && form.until
        ? { type: "until", until: new Date(`${form.until}T23:59:59`).toISOString() }
        : { type: "forever" },
  };
}

function previewNextRun(form: FormState): string | null {
  if (form.frequency === "once") return form.at ? formatScheduleTime(new Date(form.at).toISOString()) : null;
  if (form.frequency === "custom") return null;
  if (form.frequency === "weekdays") {
    // The weekday template fires on even hours only, so the preview asks the same
    // question the cron does instead of stepping a day and hoping.
    return formatScheduleTime(nextWeekdayEvenHour(new Date()).toISOString());
  }
  const [hour, minute] = form.time.split(":").map(Number);
  const candidate = new Date();
  candidate.setSeconds(0, 0);
  candidate.setHours(hour || 0, minute || 0);
  if (form.frequency === "weekly") {
    let delta = (form.weekday - candidate.getDay() + 7) % 7;
    if (delta === 0 && candidate <= new Date()) delta = 7;
    candidate.setDate(candidate.getDate() + delta);
  } else if (candidate <= new Date()) {
    candidate.setDate(candidate.getDate() + 1);
  }
  return formatScheduleTime(candidate.toISOString());
}

interface WorkspaceOption {
  id: string;
  name: string;
}

export function AutomationManager() {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("tasks");
  const [tasks, setTasks] = useState<ApiAutomationTask[]>([]);
  const [dutySchedules, setDutySchedules] = useState<ApiSchedule[]>([]);
  // One in-flight action per card; the ref makes the guard synchronous so a
  // double click cannot enqueue the same run twice before React re-renders.
  // The kind lets each control show its own pending state (the run button's
  // spinner must not appear when the switch is the one waiting).
  const [pendingActions, setPendingActions] = useState<Map<string, ScheduleCardActionKind>>(new Map());
  const pendingActionsRef = useRef<Map<string, ScheduleCardActionKind>>(new Map());
  const [records, setRecords] = useState<ApiAutomationRunRecord[]>([]);
  const [dutyRuns, setDutyRuns] = useState<DutyRun[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [dutyRunsLoading, setDutyRunsLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<ApiAutomationTask | null>(null);
  const [editingDuty, setEditingDuty] = useState<ApiSchedule | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [invalid, setInvalid] = useState<{ name?: boolean; prompt?: boolean }>({});
  const [workspaces, setWorkspaces] = useState<WorkspaceOption[]>([]);
  const [recordQuery, setRecordQuery] = useState("");
  const [recordStatus, setRecordStatus] = useState<"all" | UnifiedRecordStatus>("all");
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [expandedRecords, setExpandedRecords] = useState<Set<string>>(new Set());
  const [focusedTaskId, setFocusedTaskId] = useState<string | null>(null);
  const taskRefs = useRef<Map<string, HTMLLIElement>>(new Map());

  const showToast = useCallback((message: string) => {
    setToast(message);
  }, []);

  const beginAction = useCallback((key: string, kind: ScheduleCardActionKind = "toggle"): boolean => {
    if (pendingActionsRef.current.has(key)) return false;
    pendingActionsRef.current.set(key, kind);
    setPendingActions(new Map(pendingActionsRef.current));
    return true;
  }, []);

  const endAction = useCallback((key: string) => {
    pendingActionsRef.current.delete(key);
    setPendingActions(new Map(pendingActionsRef.current));
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      const [automation, duties] = await Promise.all([automationClient.list(), automationClient.dutySchedules()]);
      setTasks(automation);
      setDutySchedules(duties);
      setError(null);
    } catch {
      setError("自动化任务加载失败，请稍后刷新重试");
    }
  }, []);

  async function toggleDuty(duty: ApiSchedule) {
    const key = dutyKey(duty);
    if (!beginAction(key)) return;
    try {
      const updated = await assistantRequest<{ status?: string; revision?: number }>(
        `objectives/${encodeURIComponent(duty.sourceId)}/actions`, "POST", {
          action: duty.status === "active" ? "pause" : "resume",
          expectedRevision: duty.revision,
        },
      );
      const status = updated?.status ?? (duty.status === "active" ? "paused" : "active");
      const revision = updated?.revision ?? duty.revision;
      // The open panel and the card share one object; both must follow the CAS result.
      setDutySchedules((current) => current.map((item) =>
        item.sourceId === duty.sourceId ? { ...item, status, revision } : item));
      setEditingDuty((current) =>
        current && current.sourceId === duty.sourceId ? { ...current, status, revision } : current);
      await loadTasks();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务状态未更新");
    } finally {
      endAction(key);
    }
  }

  async function runDuty(duty: ApiSchedule) {
    const key = dutyKey(duty);
    if (!beginAction(key, "run")) return;
    try {
      // The endpoint deduplicates on this key, so a retried click after a
      // timeout cannot enqueue a second execution.
      await assistantRequest(
        `objectives/${encodeURIComponent(duty.sourceId)}/executions`,
        "POST",
        undefined,
        createRandomId(),
      );
      showToast("已触发立即执行，可在运行记录中查看进度");
    } catch (cause) {
      showToast(cause instanceof Error ? `触发失败：${cause.message}` : "触发失败，请稍后重试");
    } finally {
      endAction(key);
    }
  }

  async function deleteDuty(duty: ApiSchedule) {
    const key = dutyKey(duty);
    if (!beginAction(key, "delete")) return;
    setConfirmingDelete(null);
    try {
      // Objectives have no physical delete; stop is the terminal state and the
      // execution history stays attached to the objective.
      await assistantRequest(
        `objectives/${encodeURIComponent(duty.sourceId)}/actions`,
        "POST",
        { action: "stop", expectedRevision: duty.revision },
      );
      setDutySchedules((current) => current.filter((item) => item.sourceId !== duty.sourceId));
      if (editingDuty?.sourceId === duty.sourceId) closePanel();
      showToast("定时任务已删除");
    } catch (cause) {
      showToast(cause instanceof Error ? `删除失败：${cause.message}` : "删除失败，请稍后重试");
    } finally {
      endAction(key);
    }
  }

  const loadRecords = useCallback(async () => {
    setRecordsLoading(true);
    try {
      setRecords(await automationClient.records());
      setError(null);
    } catch {
      setError("运行记录加载失败，请稍后刷新重试");
    } finally {
      setRecordsLoading(false);
    }
  }, []);

  /** Duty executions live on each Objective, so the merged 运行记录 tab reads
   *  them per duty. A duty that fails to load must not hide the others. */
  const loadDutyRuns = useCallback(async (duties: ApiSchedule[]) => {
    if (duties.length === 0) {
      setDutyRuns([]);
      return;
    }
    setDutyRunsLoading(true);
    try {
      const pages = await Promise.allSettled(duties.map(async (duty) => {
        const executions = await assistantRequest<Execution[]>(
          `objectives/${encodeURIComponent(duty.sourceId)}/executions`,
        );
        return executions.map((execution) => ({ duty, execution }));
      }));
      const loaded = pages.flatMap((page) => (page.status === "fulfilled" ? page.value : []));
      setDutyRuns(loaded);
      if (pages.every((page) => page.status === "rejected")) {
        setError("定时任务执行记录加载失败，请稍后刷新重试");
      }
    } finally {
      setDutyRunsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTasks().finally(() => setLoading(false));
    automationClient.records().then(setRecords).catch(() => undefined);
    fetch("/api/spaces", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : []))
      .then((spaces: Array<{ space: { spaceId: string; name: string } }>) => {
        setWorkspaces(
          (Array.isArray(spaces) ? spaces : []).map((item) => ({
            id: item.space?.spaceId ?? "",
            name: item.space?.name ?? "未命名空间",
          })).filter((item) => item.id),
        );
      })
      .catch(() => undefined);
  }, [loadTasks]);

  // Duty executions are only read when the merged list is on screen.
  useEffect(() => {
    if (tab !== "records") return;
    void loadDutyRuns(dutySchedules);
  }, [tab, dutySchedules, loadDutyRuns]);

  function openCreate(template?: TemplateDef) {
    setEditingTask(null);
    setEditingDuty(null);
    setConfirmingDelete(null);
    setInvalid({});
    setForm(template ? formFromTemplate(template) : emptyForm());
    setModalOpen(true);
  }

  function openEdit(task: ApiAutomationTask) {
    setEditingDuty(null);
    setEditingTask(task);
    setConfirmingDelete(null);
    setInvalid({});
    setForm(formFromTask(task));
    setModalOpen(true);
  }

  /** 定时任务的编辑排程、启用/禁用都在这个面板里，卡片只放摘要和快捷操作。 */
  function openDutyPanel(duty: ApiSchedule) {
    setEditingTask(null);
    setEditingDuty(duty);
    setConfirmingDelete(null);
    setModalOpen(true);
  }

  function closePanel() {
    setModalOpen(false);
    setEditingTask(null);
    setEditingDuty(null);
    setConfirmingDelete(null);
  }

  async function submit() {
    const nextInvalid = {
      name: !form.name.trim(),
      prompt: !form.prompt.trim(),
    };
    setInvalid(nextInvalid);
    if (nextInvalid.name || nextInvalid.prompt) return;
    setSubmitting(true);
    try {
      if (editingTask) {
        const updated = await automationClient.update(editingTask.taskId, editingTask.revision, formToInput(form));
        setTasks((current) => current.map((item) => (item.taskId === updated.taskId ? updated : item)));
        showToast(`已保存，下次执行：${formatScheduleTime(updated.nextRunAt)}`);
      } else {
        const created = await automationClient.create(formToInput(form));
        setTasks((current) => [...current, created]);
        showToast(`自动化任务已创建，下次执行：${formatScheduleTime(created.nextRunAt)}`);
      }
      closePanel();
    } catch (submitError) {
      showToast(submitError instanceof Error ? `保存失败：${submitError.message}` : "保存失败，请稍后重试");
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleTask(task: ApiAutomationTask) {
    const key = taskKey(task);
    if (!beginAction(key)) return;
    try {
      const updated = task.status === "paused"
        ? await automationClient.enable(task.taskId)
        : await automationClient.pause(task.taskId);
      setTasks((current) => current.map((item) => (item.taskId === updated.taskId ? updated : item)));
      setEditingTask((current) => (current?.taskId === updated.taskId ? updated : current));
      showToast(updated.status === "paused" ? "任务已禁用" : "任务已启用");
    } catch (cause) {
      showToast(cause instanceof Error ? `操作失败：${cause.message}` : "操作失败，请稍后重试");
    } finally {
      endAction(key);
    }
  }

  async function runTask(task: ApiAutomationTask) {
    const key = taskKey(task);
    if (!beginAction(key, "run")) return;
    try {
      await automationClient.run(task.taskId);
      showToast("已触发立即执行，可在运行记录中查看进度");
      void automationClient.records().then(setRecords).catch(() => undefined);
    } catch (cause) {
      showToast(cause instanceof Error ? `触发失败：${cause.message}` : "触发失败，请稍后重试");
    } finally {
      endAction(key);
    }
  }

  async function removeTask(task: ApiAutomationTask) {
    const key = taskKey(task);
    if (!beginAction(key, "delete")) return;
    setConfirmingDelete(null);
    try {
      await automationClient.remove(task.taskId);
      setTasks((current) => current.filter((item) => item.taskId !== task.taskId));
      if (editingTask?.taskId === task.taskId) closePanel();
      showToast("定时任务已删除");
    } catch (cause) {
      showToast(cause instanceof Error ? `删除失败：${cause.message}` : "删除失败，请稍后重试");
    } finally {
      endAction(key);
    }
  }

  /** First press asks, the second performs. The key is the card identity, so
   *  the confirmation cannot leak onto another row. */
  function confirmDelete(key: string, remove: () => void) {
    if (confirmingDelete !== key) {
      setConfirmingDelete(key);
      return;
    }
    setConfirmingDelete(null);
    remove();
  }

  function openRecordConversation(record: UnifiedRecord) {
    const threadId = record.threadId || `automation_${record.sourceId}`;
    router.push(taskThreadHref(threadId, record.runId ?? undefined));
  }

  function openTaskFromRecord(record: UnifiedRecord) {
    // Ended schedules are hidden from the default list, so a record pointing
    // at one has nowhere to scroll even though the record itself is intact.
    const exists = record.source === "automation"
      ? tasks.some((task) => task.taskId === record.sourceId && !isTerminalStatus(task.status))
      : dutySchedules.some((duty) => duty.sourceId === record.sourceId && !isTerminalStatus(duty.status));
    if (!exists) {
      showToast(`任务「${record.name}」已删除或已结束，无法跳转`);
      return;
    }
    setTab("tasks");
    setFocusedTaskId(record.sourceId);
    window.setTimeout(() => {
      taskRefs.current.get(record.sourceId)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 60);
  }

  async function refreshRecords() {
    await Promise.all([loadRecords(), loadDutyRuns(dutySchedules)]);
    showToast("运行记录已刷新");
  }

  const mergedRecords = useMemo(() => mergeRunRecords(records, dutyRuns), [records, dutyRuns]);
  const filteredRecords = useMemo(
    () => filterRunRecords(mergedRecords, { status: recordStatus, query: recordQuery }),
    [mergedRecords, recordQuery, recordStatus],
  );

  // The default list is the live set; ended schedules stay reachable through
  // 运行记录 but no longer occupy a card that cannot act.
  const visibleDuties = useMemo(
    () => dutySchedules.filter((duty) => !isTerminalStatus(duty.status)),
    [dutySchedules],
  );
  const visibleTasks = useMemo(
    () => tasks.filter((task) => !isTerminalStatus(task.status)),
    [tasks],
  );
  const hasVisibleSchedules = visibleDuties.length > 0 || visibleTasks.length > 0;

  const nextRunPreview = previewNextRun(form);
  useEffect(() => {
    if (!focusedTaskId) return;
    const timer = window.setTimeout(() => setFocusedTaskId(null), 4000);
    return () => window.clearTimeout(timer);
  }, [focusedTaskId]);

  const modelOptions = [{ id: "auto", label: "Auto" }, ...MODEL_ROUTES.map((route) => ({ id: route.id, label: route.label }))];
  const permissionLabels: Record<AutomationPermissionKind, string> = {
    full: "允许完全访问",
    restricted: "受限访问（仅工作区）",
    readonly: "只读访问",
  };

  return (
    <div className={styles.manager}>
      <header className={styles.header}>
        <div className={styles.tabs} role="tablist" aria-label="自动化视图">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "tasks"}
            className={tab === "tasks" ? styles.tabActive : styles.tab}
            onClick={() => setTab("tasks")}
          >
            <ClockIcon /> 定时任务
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "records"}
            className={tab === "records" ? styles.tabActive : styles.tab}
            onClick={() => {
              setTab("records");
              void loadRecords();
            }}
          >
            <RecordsIcon /> 运行记录
          </button>
        </div>
        {tab === "records" && (
          <div className={styles.recordToolbar}>
            <select
              className={styles.statusFilter}
              value={recordStatus}
              onChange={(event) => setRecordStatus(event.target.value as typeof recordStatus)}
              aria-label="按状态筛选"
            >
              <option value="all">全部状态</option>
              {(Object.keys(RECORD_STATUS_LABELS) as UnifiedRecordStatus[]).map((status) => (
                <option key={status} value={status}>{RECORD_STATUS_LABELS[status]}</option>
              ))}
            </select>
            <input
              className={styles.search}
              type="search"
              placeholder="搜索自动化/记录"
              value={recordQuery}
              onChange={(event) => setRecordQuery(event.target.value)}
            />
            <button type="button" className={styles.refresh} title="刷新" onClick={() => void refreshRecords()}>
              <RefreshIcon />
            </button>
          </div>
        )}
        {tab === "tasks" && hasVisibleSchedules && (
          <button type="button" className={styles.headerAdd} onClick={() => openCreate()}>
            + 添加自动化
          </button>
        )}
      </header>

      <FeedbackToast message={error} tone="error" />

      {tab === "tasks" && (
        <section className={styles.section}>
          {loading ? (
            <p className={styles.loading}>正在加载自动化任务…</p>
          ) : !hasVisibleSchedules ? (
            <div className={styles.emptyTasks}>
              <span className={styles.emptyAlarm}><AlarmIcon /></span>
              <p>开启你的第一个自动化任务吧</p>
              <button type="button" className={styles.primaryAction} onClick={() => openCreate()}>
                + 添加自动化
              </button>
            </div>
          ) : (
            <ul className={styles.taskList}>
              {visibleDuties.map((duty) => {
                const key = dutyKey(duty);
                return (
                  <li
                    key={key}
                    ref={(node) => {
                      if (node) taskRefs.current.set(duty.sourceId, node);
                      else taskRefs.current.delete(duty.sourceId);
                    }}
                    className={
                      focusedTaskId === duty.sourceId
                        ? `${styles.taskCard} ${styles.taskCardFocused}`
                        : styles.taskCard
                    }
                    data-status={duty.status}
                    data-focused={focusedTaskId === duty.sourceId || undefined}
                  >
                    <button
                      type="button"
                      className={styles.taskOpen}
                      title="打开编辑面板"
                      aria-label={`编辑定时任务 ${duty.title}`}
                      onClick={() => openDutyPanel(duty)}
                    >
                      <span className={styles.taskMain}>
                        <span className={styles.taskTitleRow}>
                          <strong>{duty.title}</strong>
                          <span className={styles.statusBadge}>定时任务</span>
                          {duty.agentName && <span className={styles.agentBadge}>{duty.agentName}</span>}
                          <span className={styles.statusBadge} data-kind={duty.status}>
                            {DUTY_STATUS_LABELS[duty.status] ?? "已结束"}
                          </span>
                        </span>
                        <span className={styles.taskMeta}>
                          <span>下次执行 {formatScheduleTime(duty.nextRunAt)}</span>
                        </span>
                      </span>
                    </button>
                    <ScheduleCardActions
                      title={duty.title}
                      enabled={duty.status === "active"}
                      pending={pendingActions.has(key)}
                      pendingAction={pendingActions.get(key)}
                      confirmingDelete={confirmingDelete === key}
                      onToggle={() => void toggleDuty(duty)}
                      onRun={() => void runDuty(duty)}
                      onEdit={() => openDutyPanel(duty)}
                      onRequestDelete={() => setConfirmingDelete(key)}
                      onDelete={() => void deleteDuty(duty)}
                    />
                  </li>
                );
              })}
              {visibleTasks.map((task) => {
                const key = taskKey(task);
                return (
                  <li
                    key={key}
                    ref={(node) => {
                      if (node) taskRefs.current.set(task.taskId, node);
                      else taskRefs.current.delete(task.taskId);
                    }}
                    className={
                      focusedTaskId === task.taskId
                        ? `${styles.taskCard} ${styles.taskCardFocused}`
                        : styles.taskCard
                    }
                    data-status={task.status}
                    data-focused={focusedTaskId === task.taskId || undefined}
                  >
                    <button
                      type="button"
                      className={styles.taskOpen}
                      title="打开编辑面板"
                      aria-label={`编辑定时任务 ${task.name}`}
                      onClick={() => openEdit(task)}
                    >
                      <span className={styles.taskMain}>
                        <span className={styles.taskTitleRow}>
                          <strong>{task.name}</strong>
                          <span className={styles.statusBadge}>定时任务</span>
                          <span className={styles.statusBadge} data-kind={task.status}>
                            {task.status === "active" ? "已启用" : task.status === "paused" ? "已暂停" : "已结束"}
                          </span>
                        </span>
                        <span className={styles.taskMeta}>
                          <span>{scheduleSummary(task)}</span>
                          {task.nextRunAt && task.status === "active" && (
                            <span>下次执行：{formatScheduleTime(task.nextRunAt)}</span>
                          )}
                        </span>
                      </span>
                    </button>
                    <ScheduleCardActions
                      title={task.name}
                      enabled={task.status !== "paused"}
                      pending={pendingActions.has(key)}
                      pendingAction={pendingActions.get(key)}
                      confirmingDelete={confirmingDelete === key}
                      onToggle={() => void toggleTask(task)}
                      onRun={() => void runTask(task)}
                      onEdit={() => openEdit(task)}
                      onRequestDelete={() => setConfirmingDelete(key)}
                      onDelete={() => void removeTask(task)}
                    />
                  </li>
                );
              })}
            </ul>
          )}

          {/* 模板是空列表的起步引导；已经有任务时它只会把真正的列表挤下去。 */}
          {!loading && !hasVisibleSchedules && (
            <>
              <h2 className={styles.templatesTitle}>自动化任务模版</h2>
              <div className={styles.templateGrid}>
                {TEMPLATES.map((template) => (
                  <button
                    key={template.id}
                    type="button"
                    className={styles.templateCard}
                    onClick={() => openCreate(template)}
                  >
                    <span className={styles.templateIcon} aria-hidden="true">{template.icon}</span>
                    <span className={styles.templateBody}>
                      <strong>{template.name}</strong>
                      <small>{template.description}</small>
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
        </section>
      )}

      {tab === "records" && (
        <section className={styles.section}>
          {filteredRecords.length === 0 ? (
            <div className={styles.emptyRecords}>
              <span className={styles.emptyDoc}><RecordsIcon /></span>
              <p>{recordsLoading || dutyRunsLoading ? "正在加载运行记录…" : "暂无运行记录"}</p>
            </div>
          ) : (
            <ul className={styles.recordList}>
              {filteredRecords.map((record) => (
                <li key={record.id} className={styles.recordCard} data-status={record.status}>
                  <div className={styles.recordMain}>
                    <div className={styles.taskTitleRow}>
                      <button
                        type="button"
                        className={styles.recordTaskLink}
                        title="打开这次执行所在的会话"
                        onClick={() => openRecordConversation(record)}
                      >
                        {record.name}
                      </button>
                      <button
                        type="button"
                        className={styles.recordTaskAction}
                        title="在定时任务中定位该任务"
                        onClick={() => openTaskFromRecord(record)}
                      >
                        定位任务
                      </button>
                      <span className={styles.recordBadge} data-kind={record.status}>
                        {RECORD_STATUS_LABELS[record.status]}
                      </span>
                      <small className={styles.triggerTag}>
                        定时任务 · {record.trigger}
                      </small>
                    </div>
                    <p className={styles.recordMeta}>
                      <span>开始：{formatScheduleTime(record.startedAt)}</span>
                      {record.finishedAt && <span>结束：{formatScheduleTime(record.finishedAt)}</span>}
                      {record.durationMs != null && <span>耗时：{formatDuration(record.durationMs)}</span>}
                    </p>
                    {record.summary && (
                      <button
                        type="button"
                        className={`${styles.recordOutput} ${expandedRecords.has(record.id) ? styles.recordOutputExpanded : ""}`}
                        onClick={() =>
                          setExpandedRecords((current) => {
                            const next = new Set(current);
                            if (next.has(record.id)) next.delete(record.id);
                            else next.add(record.id);
                            return next;
                          })
                        }
                      >
                        {expandedRecords.has(record.id)
                          ? record.summary
                          : record.summary.length > 80
                            ? `${record.summary.slice(0, 80)}…（点击展开）`
                            : record.summary}
                      </button>
                    )}
                    {record.artifacts && record.artifacts.length > 0 && (
                      <div className={styles.recordArtifacts}>
                        <span className={styles.recordArtifactsLabel}>产出</span>
                        {record.artifacts.map((artifact) => (
                          <span key={artifact.artifactId} className={styles.recordArtifact}>
                            <FileIcon /> {artifact.name}
                            {artifact.sizeBytes != null && (
                              <small>{Math.max(1, Math.round(artifact.sizeBytes / 1024))} KB</small>
                            )}
                          </span>
                        ))}
                      </div>
                    )}
                    {record.error && <p className={styles.recordError}>失败原因：{record.error}</p>}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {modalOpen && (
        <div className={styles.overlay} onClick={closePanel}>
          <div
            className={styles.modal}
            role="dialog"
            aria-modal="true"
            aria-label={editingDuty || editingTask ? "编辑定时任务" : "添加自动化任务"}
            onClick={(event) => event.stopPropagation()}
          >
            <header className={styles.modalHeader}>
              <h2>{editingDuty || editingTask ? "编辑定时任务" : "添加自动化任务"}</h2>
              <button type="button" className={styles.modalClose} aria-label="关闭" onClick={closePanel}>✕</button>
            </header>

            {editingDuty ? (
              <>
                <div className={styles.panelSummary}>
                  <strong>{editingDuty.title}</strong>
                  <p>
                    <span>{DUTY_STATUS_LABELS[editingDuty.status] ?? "已结束"}</span>
                    <span>下次执行 {formatScheduleTime(editingDuty.nextRunAt)}</span>
                  </p>
                </div>
                <div className={styles.field}>
                  <span className={styles.fieldLabel}>编辑排程</span>
                  <DutyScheduleControl
                    key={editingDuty.revision}
                    duty={botDutyFromSchedule(editingDuty)}
                    onSaved={(saved) => {
                      // Keep the open panel on the fresh revision so the next save
                      // still passes the Objective's CAS check.
                      setEditingDuty((current) => current && current.sourceId === saved.objectiveId
                        ? {
                            ...current,
                            title: saved.title,
                            status: saved.status,
                            revision: saved.revision,
                            timing: {
                              kind: saved.schedule?.kind ?? current.timing.kind,
                              at: saved.schedule?.at ?? null,
                              seconds: saved.schedule?.intervalMinutes == null
                                ? null
                                : saved.schedule.intervalMinutes * 60,
                              expression: saved.schedule?.expression ?? null,
                              timezone: saved.schedule?.timezone ?? current.timing.timezone,
                              meetingStartsAt: saved.schedule?.meetingStartsAt ?? null,
                              minutesBefore: saved.schedule?.minutesBefore ?? null,
                            },
                          }
                        : current);
                      void loadTasks();
                    }}
                  />
                </div>
                {editingDuty.status !== "active" && editingDuty.status !== "paused" && (
                  <p className={styles.panelNote}>定时任务已结束，排程只读。</p>
                )}
              </>
            ) : (
              <>
            <div className={styles.field}>
              <label htmlFor="automation-name">名称</label>
              <input
                id="automation-name"
                type="text"
                placeholder="输入任务名称"
                value={form.name}
                data-invalid={invalid.name || undefined}
                className={invalid.name ? styles.inputInvalid : undefined}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
              {invalid.name && <small className={styles.fieldError}>请输入任务名称</small>}
            </div>

            <div className={styles.field}>
              <label htmlFor="automation-prompt">提示词</label>
              <div className={styles.promptBox}>
                <textarea
                  id="automation-prompt"
                  placeholder="添加提示词"
                  value={form.prompt}
                  className={invalid.prompt ? styles.inputInvalid : undefined}
                  onChange={(event) => setForm((current) => ({ ...current, prompt: event.target.value }))}
                />
                <footer className={styles.promptFooter}>
                  <button type="button" className={styles.promptAdd} title="插入引用" disabled>+</button>
                  <label className={styles.modelPicker}>
                    <AutoIcon />
                    <select
                      value={form.model}
                      onChange={(event) => setForm((current) => ({ ...current, model: event.target.value }))}
                      aria-label="执行模型"
                    >
                      {modelOptions.map((option) => (
                        <option key={option.id} value={option.id}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                </footer>
              </div>
              {invalid.prompt && <small className={styles.fieldError}>请添加提示词</small>}
            </div>

            <div className={styles.fieldRow}>
              <label className={styles.inlineField}>
                <WorkspaceIcon />
                <select
                  value={form.workspaceId}
                  onChange={(event) => setForm((current) => ({ ...current, workspaceId: event.target.value }))}
                  aria-label="选择工作空间"
                >
                  <option value="">选择工作空间</option>
                  {workspaces.map((workspace) => (
                    <option key={workspace.id} value={workspace.id}>{workspace.name}</option>
                  ))}
                </select>
              </label>
              <label className={form.permission === "full" ? styles.inlineDanger : styles.inlineField}>
                <WarnIcon />
                <select
                  value={form.permission}
                  onChange={(event) => setForm((current) => ({ ...current, permission: event.target.value as AutomationPermissionKind }))}
                  aria-label="执行权限"
                >
                  {(Object.keys(permissionLabels) as AutomationPermissionKind[]).map((kind) => (
                    <option key={kind} value={kind}>{permissionLabels[kind]}</option>
                  ))}
                </select>
              </label>
            </div>

            <div className={styles.fieldRow}>
              <div className={styles.inlineFieldPlain}>
                <span>执行频率：</span>
                <select
                  value={form.frequency}
                  onChange={(event) => setForm((current) => ({ ...current, frequency: event.target.value as FrequencyType }))}
                  aria-label="执行频率"
                >
                  <option value="once">单次</option>
                  <option value="daily">每天</option>
                  <option value="weekly">每周</option>
                  <option value="weekdays">工作日每 2 小时</option>
                  <option value="custom">自定义 cron</option>
                </select>
                {form.frequency === "once" && (
                  <input
                    type="datetime-local"
                    value={form.at}
                    onChange={(event) => setForm((current) => ({ ...current, at: event.target.value }))}
                    aria-label="单次执行时间"
                  />
                )}
                {form.frequency === "daily" && (
                  <input
                    type="time"
                    value={form.time}
                    onChange={(event) => setForm((current) => ({ ...current, time: event.target.value }))}
                    aria-label="每天执行时间"
                  />
                )}
                {form.frequency === "weekly" && (
                  <>
                    <select
                      value={form.weekday}
                      onChange={(event) => setForm((current) => ({ ...current, weekday: Number(event.target.value) }))}
                      aria-label="每周执行日"
                    >
                      {WEEKDAY_LABELS.map((label, index) => (
                        <option key={label} value={index}>{label}</option>
                      ))}
                    </select>
                    <input
                      type="time"
                      value={form.time}
                      onChange={(event) => setForm((current) => ({ ...current, time: event.target.value }))}
                      aria-label="每周执行时间"
                    />
                  </>
                )}
                {form.frequency === "custom" && (
                  <input
                    type="text"
                    value={form.cron}
                    placeholder="0 9 * * *"
                    onChange={(event) => setForm((current) => ({ ...current, cron: event.target.value }))}
                    aria-label="cron 表达式"
                  />
                )}
              </div>
              <div className={styles.inlineFieldPlain}>
                <span>有效期：</span>
                <select
                  value={form.validity}
                  onChange={(event) => setForm((current) => ({ ...current, validity: event.target.value as FormState["validity"] }))}
                  aria-label="有效期"
                >
                  <option value="forever">长期有效</option>
                  <option value="until">至某日期</option>
                </select>
                {form.validity === "until" && (
                  <input
                    type="date"
                    value={form.until}
                    onChange={(event) => setForm((current) => ({ ...current, until: event.target.value }))}
                    aria-label="有效期截止日期"
                  />
                )}
              </div>
            </div>

            {nextRunPreview && (
              <p className={styles.nextRunPreview}>下次执行：{nextRunPreview}（时区：{localTimezone()}）</p>
            )}
              </>
            )}

            <footer className={styles.modalFooter}>
              {editingDuty ? (
                <>
                  {(editingDuty.status === "active" || editingDuty.status === "paused") && (
                    <button
                      type="button"
                      className={styles.cancelButton}
                      disabled={pendingActions.has(dutyKey(editingDuty))}
                      onClick={() => void toggleDuty(editingDuty)}
                    >
                      {editingDuty.status === "paused" ? "启用" : "禁用"}
                    </button>
                  )}
                  <button type="button" className={styles.confirmButton} onClick={closePanel}>关闭</button>
                </>
              ) : editingTask ? (
                <>
                  <button type="button" className={styles.cancelButton} onClick={() => void runTask(editingTask)}>
                    立即执行
                  </button>
                  <button type="button" className={styles.cancelButton} onClick={() => void toggleTask(editingTask)}>
                    {editingTask.status === "paused" ? "启用" : "禁用"}
                  </button>
                  <button
                    type="button"
                    className={`${styles.cancelButton} ${styles.dangerAction}`}
                    onClick={() => confirmDelete(taskKey(editingTask), () => void removeTask(editingTask))}
                  >
                    {confirmingDelete === taskKey(editingTask) ? "确认删除？" : "删除"}
                  </button>
                  <button type="button" className={styles.cancelButton} onClick={closePanel}>取消</button>
                  <button type="button" className={styles.confirmButton} disabled={submitting} onClick={() => void submit()}>
                    {submitting ? "保存中…" : "保存"}
                  </button>
                </>
              ) : (
                <>
                  <button type="button" className={styles.cancelButton} onClick={closePanel}>取消</button>
                  <button type="button" className={styles.confirmButton} disabled={submitting} onClick={() => void submit()}>
                    {submitting ? "保存中…" : "确定"}
                  </button>
                </>
              )}
            </footer>
          </div>
        </div>
      )}

      <FeedbackToast message={toast} tone={/失败/.test(toast ?? "") ? "error" : "info"} onDismiss={() => setToast(null)} />
    </div>
  );
}

function ClockIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10.5" r="6" />
      <path d="M10 7.5v3l2 1.5M4 4 2.8 5.4M16 4l1.2 1.4" />
    </svg>
  );
}

function RecordsIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5 3.5h10v13H5z" />
      <path d="M7.5 7h5m-5 3h5m-5 3h3" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M15.5 10a5.5 5.5 0 1 1-1.6-3.9" />
      <path d="M15.8 3.2v3.2h-3.2" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5.5 3.5h6l3 3v10h-9z" />
      <path d="M11.5 3.5v3h3" />
    </svg>
  );
}

function AlarmIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10.5" r="6" />
      <path d="M10 7.5v3l2 1.5M4 4 2.8 5.4M16 4l1.2 1.4" />
    </svg>
  );
}

function WorkspaceIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3.5 6.5 10 3.5l6.5 3v7l-6.5 3-6.5-3z" />
      <path d="M3.5 6.5 10 9.7l6.5-3.2M10 9.7v6.6" />
    </svg>
  );
}

function WarnIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="6.5" />
      <path d="M10 6.5v4m0 2.6v.4" />
    </svg>
  );
}

function AutoIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="6.5" />
      <path d="m6.8 12.5 2-5 2 5m-3.3-1.4h2.6m3.4 1.4-1.3-3.3" />
    </svg>
  );
}
