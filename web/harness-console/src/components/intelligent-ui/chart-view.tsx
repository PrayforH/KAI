"use client";

import { useState } from "react";

import { type ChartRow, cellText, formatNumber } from "./cells";
import type { ChartConfig } from "./schema";
import { ChartDataTable } from "./table-view";

const WIDTH = 640;
const HEIGHT = 260;
const PADDING = { top: 16, right: 16, bottom: 44, left: 56 };
const SERIES_COLOURS = ["var(--codex-accent)", "#6aa9ff", "#7dd3a0", "#e0a458"];

type Series = { label: string; values: (number | null)[] };

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

function shortLabel(value: string, maximum = 10): string {
  return value.length > maximum ? `${value.slice(0, maximum - 1)}…` : value;
}

/** Charting without a chart library: four types are small enough to draw
 * directly, and the console's bundle budget is a stated constraint. */
export function ChartView({
  rows,
  config,
  title,
}: {
  rows: ChartRow[];
  config: ChartConfig;
  title?: string | null;
}) {
  const [showData, setShowData] = useState(false);

  const categories = rows.map((row) => cellText(row[config.x_field]));
  const series: Series[] = config.y_fields.map((field, index) => ({
    label: config.y_labels?.[index] ?? field,
    values: rows.map((row) => {
      const raw = row[field];
      return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
    }),
  }));
  const unit = config.unit ?? "";
  const missing = series.reduce(
    (count, item) => count + item.values.filter((value) => value === null).length,
    0,
  );
  const chartBottom = HEIGHT - PADDING.bottom;
  const chartRight = WIDTH - PADDING.right;

  const plot = () => {
    if (config.chart_type === "pie") {
      const values = series[0].values.map((value) => value ?? 0);
      const total = values.reduce((sum, value) => sum + value, 0);
      const radius = Math.min(WIDTH, HEIGHT) / 2 - PADDING.top - 8;
      const centreX = 130;
      const centreY = HEIGHT / 2;
      let angle = -Math.PI / 2;
      return (
        <>
          {values.map((value, index) => {
            const sweep = total > 0 ? (value / total) * Math.PI * 2 : 0;
            const next = angle + sweep;
            const x1 = centreX + radius * Math.cos(angle);
            const y1 = centreY + radius * Math.sin(angle);
            const x2 = centreX + radius * Math.cos(next);
            const y2 = centreY + radius * Math.sin(next);
            const path =
              sweep > 0
                ? `M ${centreX} ${centreY} L ${x1} ${y1} A ${radius} ${radius} 0 ${sweep > Math.PI ? 1 : 0} 1 ${x2} ${y2} Z`
                : "";
            angle = next;
            return (
              <path
                key={index}
                d={path}
                fill={SERIES_COLOURS[index % SERIES_COLOURS.length]}
                opacity={0.85}
              />
            );
          })}
          {series[0].values.map((value, index) => (
            <g key={index} transform={`translate(${centreX + radius + 28}, ${PADDING.top + index * 24})`}>
              <rect width="10" height="10" fill={SERIES_COLOURS[index % SERIES_COLOURS.length]} />
              <text x="16" y="9" className="intelligent-ui-legend-text">
                {shortLabel(categories[index], 12)} · {formatNumber(cellText(value), unit)}
              </text>
            </g>
          ))}
        </>
      );
    }

    const maximum = niceMax(
      Math.max(...series.flatMap((item) => item.values.map((value) => value ?? 0)), 0),
    );
    const slot = (chartRight - PADDING.left) / Math.max(categories.length, 1);
    const scaleY = (value: number) => chartBottom - (value / maximum) * (chartBottom - PADDING.top);
    const ticks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => fraction * maximum);
    return (
      <>
        {ticks.map((tick, index) => (
          <g key={index}>
            <line
              x1={PADDING.left}
              x2={chartRight}
              y1={scaleY(tick)}
              y2={scaleY(tick)}
              className="intelligent-ui-grid"
            />
            <text x={PADDING.left - 8} y={scaleY(tick) + 4} textAnchor="end" className="intelligent-ui-tick">
              {tick.toLocaleString("zh-Hans-CN", { maximumFractionDigits: 2 })}
            </text>
          </g>
        ))}
        {config.chart_type === "bar"
          ? series.flatMap((item, seriesIndex) =>
              item.values.map((value, index) => {
                if (value === null) return null;
                const barWidth = (slot * 0.7) / series.length;
                const x = PADDING.left + index * slot + slot * 0.15 + seriesIndex * barWidth;
                return (
                  <rect
                    key={`${seriesIndex}-${index}`}
                    x={x}
                    y={scaleY(value)}
                    width={Math.max(barWidth - 2, 1)}
                    height={Math.max(chartBottom - scaleY(value), 0)}
                    fill={SERIES_COLOURS[seriesIndex % SERIES_COLOURS.length]}
                    rx="3"
                  />
                );
              })
            )
          : series.map((item, seriesIndex) => {
              const points = item.values
                .map((value, index) =>
                  value === null
                    ? null
                    : `${PADDING.left + index * slot + slot / 2},${scaleY(value)}`,
                )
                .filter((point): point is string => point !== null);
              const colour = SERIES_COLOURS[seriesIndex % SERIES_COLOURS.length];
              return (
                <g key={seriesIndex}>
                  {config.chart_type === "area" && points.length > 1 ? (
                    <polygon
                      points={`${PADDING.left + slot / 2},${chartBottom} ${points.join(" ")} ${PADDING.left + (points.length - 1) * slot + slot / 2},${chartBottom}`}
                      fill={colour}
                      opacity={0.18}
                    />
                  ) : null}
                  <polyline points={points.join(" ")} fill="none" stroke={colour} strokeWidth="2" />
                </g>
              );
            })}
        {categories.map((category, index) => (
          <text
            key={index}
            x={PADDING.left + index * slot + slot / 2}
            y={chartBottom + 18}
            textAnchor="middle"
            className="intelligent-ui-tick"
          >
            {shortLabel(category)}
          </text>
        ))}
      </>
    );
  };

  return (
    <div className="intelligent-ui-block intelligent-ui-chart">
      <div className="intelligent-ui-toolbar">
        <span className="intelligent-ui-count">
          {config.y_label || config.y_fields.join(" / ")}
          {unit ? `（${unit}）` : ""}
        </span>
        <span className="intelligent-ui-toolbar-actions">
          {series.length > 1 ? (
            <span className="intelligent-ui-legend">
              {series.map((item, index) => (
                <span key={item.label}>
                  <i style={{ background: SERIES_COLOURS[index % SERIES_COLOURS.length] }} />
                  {shortLabel(item.label, 12)}
                </span>
              ))}
            </span>
          ) : null}
          <button type="button" onClick={() => setShowData((current) => !current)}>
            {showData ? "隐藏数据" : "查看数据"}
          </button>
        </span>
      </div>
      <svg
        className="intelligent-ui-canvas"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={title ?? config.y_label ?? config.y_fields.join(" / ")}
      >
        {plot()}
        {config.x_label ? (
          <text
            x={(PADDING.left + chartRight) / 2}
            y={HEIGHT - 6}
            textAnchor="middle"
            className="intelligent-ui-axis-title"
          >
            {config.x_label}
          </text>
        ) : null}
      </svg>
      {missing > 0 ? (
        <p className="intelligent-ui-note">有 {missing} 个缺失值未绘制。</p>
      ) : null}
      {showData ? (
        <ChartDataTable
          rows={rows}
          xField={config.x_field}
          yFields={config.y_fields}
          yLabels={config.y_labels}
          unit={unit}
          xLabel={config.x_label}
        />
      ) : null}
    </div>
  );
}
