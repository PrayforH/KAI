"use client";

export type CheckpointMetrics =
  | {
      tokens?: number | null;
      costUsd?: number | null;
      unknownUsage?: boolean | null;
      skippedNoModel?: boolean | null;
    }
  | null
  | undefined;

export type CheckpointMeteringView = {
  text: string;
  state: "measured" | "unknown" | "skipped";
};

/**
 * 检查点计量的唯一文案口径（托管面板与助理面进展浮标共用）：
 * - 有 tokens → 「tokens 1,234」（zh-CN 千分位）；
 * - 用量未知 → 「tokens 未计量」（绝不显示 0）；
 * - cost / 耗时只在有值时追加。
 * - skipUnchanged 早退（未唤起模型）单独一档：不是"未计量"，也不显示 0。
 * 两者都没有 → null（调用方不渲染计量行，兼容历史载荷）。
 */
export function formatCheckpointMetering(
  metrics: CheckpointMetrics,
  durationSeconds?: number | null,
): CheckpointMeteringView | null {
  if (metrics?.skippedNoModel) {
    const parts = ["未唤起模型 · 无变化"];
    if (durationSeconds != null) {
      parts.push(`耗时 ${Math.max(1, Math.round(durationSeconds))} 秒`);
    }
    return { text: parts.join(" · "), state: "skipped" };
  }
  const tokens = metrics?.tokens ?? null;
  const unknownUsage = Boolean(metrics && (metrics.unknownUsage || tokens == null));
  const parts: string[] = [];
  if (metrics) {
    parts.push(
      unknownUsage ? "tokens 未计量" : `tokens ${tokens!.toLocaleString("zh-CN")}`,
    );
  }
  if (metrics?.costUsd != null) parts.push(`cost $${metrics.costUsd}`);
  if (durationSeconds != null) {
    parts.push(`耗时 ${Math.max(1, Math.round(durationSeconds))} 秒`);
  }
  if (!parts.length) return null;
  return {
    text: parts.join(" · "),
    state: metrics && !unknownUsage ? "measured" : "unknown",
  };
}

/** 一行短计量；无计量时返回 null（不占位、不显示假 0）。 */
export function CheckpointMeteringLine({
  metrics,
  durationSeconds,
  className,
}: {
  metrics: CheckpointMetrics;
  durationSeconds?: number | null;
  className?: string;
}) {
  const view = formatCheckpointMetering(metrics, durationSeconds);
  if (!view) return null;
  return (
    <p className={className} data-metering={view.state}>
      {view.text}
    </p>
  );
}
