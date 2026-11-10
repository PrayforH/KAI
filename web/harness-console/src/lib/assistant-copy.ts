/** Display projection only. Durable messages, links and tool facts are unchanged. */
const idSource = String.raw`\b(?:(?:assistant_)?(?:specialist|objective)|managed(?:_notice)?|execution|handover|checkpoint|run|thread|artifact)_[a-f0-9]{8,}\b`;
const internalId = () => new RegExp(idSource, "gi");
const technical = /\b(?:revision\s*[=:：]?\s*\d+|expected_revision|notify_on_progress|notification_error|managed_id|objective_id|thread_id)\b/i;
const problem = /失败|未成功|未完成|尚未|未启动|缺少|待补|需要你|请你|错误|异常|冲突|超时|无法|不能|未确认/;

export function assistantCopy(text: string): string {
  // Destinations stay usable, but never become visible metadata in the copy.
  const urls: string[] = [];
  let value = text.replace(/\]\(([^)\n]*)\)/g, (_, url: string) => `](\uE000${urls.push(url)-1}\uE001)`);
  value = value.split("\n").filter(line => {
    if (problem.test(line)) return true;
    return !((internalId().test(line) || technical.test(line)) &&
      /^\s*(?:[-*\d.]+\s*)?(?:已解析|准确原|关注关系为|对应.*(?:线程|职责)|绑定线程|revision|expected_revision|managed_id|objective_id|thread_id)/i.test(line));
  }).join("\n");
  value = value
    .replace(/[（(]([^()（）\n]*)[)）]/g, (whole, body: string) =>
      (internalId().test(body) || technical.test(body)) && !problem.test(body) ? "" : whole)
    .replace(/`?\bnotify_on_progress`?\s*(?:为|是|[=:：])?\s*`?(true|false)`?/gi,
      (_, enabled: string) => enabled.toLowerCase() === "true" ? "结果通知已开启" : "结果通知已关闭")
    .replace(/`?\bnotification_error`?\s*(?:为|是|[=:：])?\s*(?:空|null|None|""|''|`{2})/gi, "")
    .replace(/`?\b(?:expected_)?revision`?\s*(?:仍|为|是|[=:：])?\s*`?\d+`?/gi, "")
    .replace(/`?(?:\b(?:thread|objective|managed|specialist|execution|checkpoint|run|artifact)(?:_id|Id| ID)?)`?\s*[=:：]\s*(?=`?(?:assistant_|specialist_|objective_|managed_|execution_|run_|thread_|artifact_))/gi, "")
    .replace(internalId(), "")
    .replace(/\b(?:assistant_route_message|assistant_resolve_target|assistant_manage_work|assistant_delegate_to_specialist|assistant_work_notifications|assistant_update_task|assistant_task_status|assistant_send_instruction|assistant_create_task)\b/g, "")
    .replace(/\bfollow_only\b/g, "只跟进").replace(/\bautonomous\b/g, "自动推进")
    .replace(/`?\[?(?:needs_input|pending_input)\]?`?/g, "等你补充信息")
    .replace(/\[ready\]/g, "可接单")
    .replace(/\bsucceeded\b/g, "本次处理已结束")
    .replace(/\bqueued\b/g, "已排队").replace(/\brunning\b/g, "正在处理")
    .replace(/\bwaiting_approval\b/g, "等待你确认")
    .replace(/\bfailed\b/g, "处理失败").replace(/\btimed_out\b/g, "处理超时")
    .replace(/\brejected\b/g, "未获批准").replace(/\bcancelled\b/g, "已停止")
    .replace(/\bschedule_minutes\b/g, "执行频率").replace(/\bnotify_channel\b/g, "通知方式")
    .replace(/\b(?:managed_id|objective_id|thread_id)\b\s*`?\s*[,，；;]/g, "")
    .replace(/\bobjective\s*状态(?:为|是)\s*active\b/g, "事项仍在跟进")
    .replace(/\bmode\s*=\s*/g, "")
    .replace(/职责线程/g, "对应事项")
    .replace(/原线程/g, "原对话")
    .replace(/`\s*`|[（(]\s*[)）]/g, "")
    .replace(/[，,；;]\s*(?=[，,；;。])/g, "")
    .replace(/^[ \t]*[-*]?[ \t]*[，,；;。]*[ \t]*$/gm, "")
    .replace(/[ \t]+([，。；：])/g, "$1")
    .replace(/\n{3,}/g, "\n\n").trim();
  return value.replace(/\uE000(\d+)\uE001/g, (_, index: string) => urls[Number(index)] ?? "");
}

/** Metadata-only before/after widgets are not user-facing result cards. */
export function isInternalSummary(value: {kind: string; data: Record<string, unknown>}): boolean {
  if (value.kind !== "summary" || !Array.isArray(value.data.items) || !value.data.items.length) return false;
  return value.data.items.every(item => {
    if (!item || typeof item !== "object") return false;
    const text = JSON.stringify(item);
    return internalId().test(text) || technical.test(text);
  });
}

export function compactAssistantProgress(text: string): string {
  const clean = assistantCopy(text);
  if (clean.length <= 420) return clean;
  const paragraphs = clean.split(/\n\s*\n/);
  const lead = paragraphs[0].slice(0, 260);
  const attention = paragraphs.slice(1).filter(line => problem.test(line)).slice(0, 2)
    .map(line => line.slice(0, 240));
  return [lead, ...attention].join("\n\n");
}

export function assistantPresentation<T extends {kind: string; data: Record<string, unknown>; title?: string | null; fallback_text: string}>(item: T, {hasAttachments = false}: {hasAttachments?: boolean} = {}): T | null {
  if (item.kind === "form") return item;
  // File delivery already has a preview/download capsule. An additional
  // summary repeats its metadata and exposes publication/transport details.
  if (hasAttachments && item.kind === "summary") return null;
  if (isInternalSummary(item)) return null;
  const clean = (value: unknown): unknown => {
    if (typeof value === "string") return /^https?:\/\//.test(value) ? value : assistantCopy(value);
    if (Array.isArray(value)) return value.map(clean);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, val]) => [key, clean(val)]));
    return value;
  };
  const data = clean(item.data) as Record<string, unknown>;
  if (item.kind === "table" && Array.isArray(data.columns)) {
    const columns = data.columns.filter(column => column && typeof column === "object" &&
      !/^(?:revision|(?:expected_)?revision|thread(?:_id)?|objective(?:_id)?|managed(?:_id)?|notify_on_progress|notification_error)$/i.test(String((column as {key?: string}).key)));
    if (!columns.length) return null;
    data.columns = columns;
  }
  return {...item, data, title: item.title ? assistantCopy(item.title) : item.title, fallback_text: assistantCopy(item.fallback_text)};
}

/** Coordination uses short chat receipts; reports and business answers keep
 * their own content. Never convert a failed or waiting reply into success. */
export function compactAssistantReceipt(text: string): string {
  const clean = assistantCopy(text);
  const lines = clean.split(/\n+/).map(line => line.replace(/^\s*[-*\d.]+\s*/, "").trim())
    .filter(line => line && !/^(?:只读查询结果|查询结果|转交结果)[：:]?$/.test(line));
  if (!lines.length) return "";
  const facts = lines.flatMap(line => line.match(/[^。！？]+[。！？]?/g) ?? [])
    .map(line => line.trim()).filter(Boolean);
  const lead = facts.slice(0, 2);
  // Keep later failures and concrete input requests visible as well.
  const attention = facts.slice(2).filter(line => problem.test(line));
  const kept = [...new Set([...lead, ...attention])];
  // 一句「需要你补：」后面跟着的清单就是这条收据要传达的事实；
  // 只留冒号会把请求截成一句没下文的半句话。
  const follow = kept.filter(line => /[：:]$/.test(line)).flatMap(line => {
    const start = facts.indexOf(line);
    if (start < 0) return [];
    const items: string[] = [];
    for (let index = start + 1; index < facts.length && items.length < 6; index++) {
      const item = facts[index]!;
      if (/[：:]$/.test(item)) break;
      items.push(item);
    }
    return items;
  });
  const result = [...new Set([...kept, ...follow])];
  return result.join("\n\n");
}

/** Old quoted receipts can contain a truncated Markdown source URL. Keep the
 * quoted business text, not its obsolete system footer or raw coordinates. */
export function assistantQuoteCopy(text: string): string {
  return assistantCopy(text
    .replace(/\[(?:回到原对话|查看失败原因)\]\(\/(?:assistant\?|\?)[^)]*(?:\)|$)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1"));
}
