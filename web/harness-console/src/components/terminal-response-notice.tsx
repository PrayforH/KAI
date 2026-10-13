import type { RunActivity } from "../lib/activity-schema";
import styles from "./terminal-response-notice.module.css";

/** A platform status, not invented model prose or an automatic restart. */
export function TerminalResponseNotice({ activity, hasResponse }: {
  activity?: RunActivity;
  hasResponse: boolean;
}) {
  if (!activity || hasResponse) return null;
  const titles: Record<string, string> = {
    cancelled: "本次运行已停止，未生成最终回答。",
    failed: "本次运行失败，未生成最终回答。",
    timed_out: "本次运行超时，未生成最终回答。",
    rejected: "本次运行被拒绝，未生成最终回答。",
    succeeded: "运行已结束，但未收到可展示的最终回答。",
  };
  const title = titles[activity.status];
  if (!title) return null;
  const results = new Map(activity.items.filter(item => item.event_type === "tool.result")
    .map(item => [String(item.metadata.tool_call_id ?? item.id), item]));
  const failed = [...results.values()].filter(item => item.status === "failed").length;
  return <section className={styles.notice} role="status" aria-label="本次运行结果">
    <p className={styles.title}>{title}</p>
    {results.size > 0 && <p>结束前已返回 {results.size} 次工具结果{failed ? `，其中 ${failed} 次报错` : ""}。这些返回不等于已完成核实，已取得内容可在执行过程中查看。</p>}
    <p>本次尚未完成结果整理；如需继续，可发送补充消息。</p>
  </section>;
}
