"use client";

import { useState } from "react";

import { type ChartRow, cellText, formatNumber } from "./cells";
import type { ChartConfig } from "./schema";
import { ChartDataTable } from "./table-view";

const WIDTH = 640;
const HEIGHT = 260;
const PADDING = { top: 16, right: 16, bottom: 44, left: 56 };
const SERIES_COLOURS = ["var(--codex-accent)", "#6aa9ff", "#7dd3a0", "#e0a458"];
const TICK_FRACTIONS = [0, 0.25, 0.5, 0.75, 1];

type Series = { label: string; values: (number | null)[] };

/** Charting without a chart library: the four supported types are small enough
 * to draw directly, and the console's bundle budget is a stated constraint. */
export function ChartView({
  rows,
  config,
  title,
  showData: savedShowData,
  onShowData,
}: {
  rows: ChartRow[];
  config: ChartConfig;
  title?: string | null;
  showData?: boolean;
  onShowData?: (value: boolean) => void;
}) {
  const [localShowData, setShowData] = useState(false);
  const showData = savedShowData ?? localShowData;

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
    (count, item) =>
      count + item.values.filter((value) => value === null).length,
    0,
  );
  const height =
    config.chart_type === "pie"
      ? Math.max(HEIGHT, rows.length * 24 + 32)
      : HEIGHT;

  return (
    <div className="intelligent-ui-block intelligent-ui-chart">
      <div className="intelligent-ui-toolbar">
        <span className="intelligent-ui-count">
          {config.y_label || series.map((item) => item.label).join(" / ")}
          {unit ? `（${unit}）` : ""}
        </span>
        <span className="intelligent-ui-toolbar-actions">
          {series.length > 1 ? (
            <span className="intelligent-ui-legend">
              {series.map((item, index) => (
                <span key={item.label}>
                  <i
                    style={{
                      background: SERIES_COLOURS[index % SERIES_COLOURS.length],
                    }}
                  />
                  {shortLabel(item.label, 12)}
                </span>
              ))}
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => {
              setShowData(!showData);
              onShowData?.(!showData);
            }}
          >
            {showData ? "隐藏数据" : "查看数据"}
          </button>
        </span>
      </div>
      <svg
        className="intelligent-ui-canvas"
        viewBox={`0 0 ${WIDTH} ${height}`}
        role="img"
        aria-label={title ?? config.y_label ?? config.y_fields.join(" / ")}
      >
        <Plot categories={categories} series={series} config={config} />
      </svg>
      {missing > 0 ? (
        <p className="intelligent-ui-note">有 {missing} 个缺失值未绘制。</p>
      ) : null}
      {config.chart_type === "pie" &&
      !series[0]?.values.some((value) => value !== null && value > 0) ? (
        <p className="intelligent-ui-note">
          没有可绘制的正数份额，请查看原始数据。
        </p>
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

function Plot({
  categories,
  series,
  config,
}: {
  categories: string[];
  series: Series[];
  config: ChartConfig;
}) {
  if (config.chart_type === "pie") {
    return (
      <PieSlices
        values={series[0].values}
        categories={categories}
        unit={config.unit ?? ""}
      />
    );
  }
  const values = series.flatMap((item) =>
    item.values.filter((v): v is number => v !== null),
  );
  const minimum = -niceBound(-Math.min(...values, 0));
  const maximum = niceBound(Math.max(...values, 0)) || (minimum === 0 ? 1 : 0);
  const chartBottom = HEIGHT - PADDING.bottom;
  const slot =
    (WIDTH - PADDING.right - PADDING.left) / Math.max(categories.length, 1);
  const scaleY = (value: number) =>
    chartBottom -
    ((value - minimum) / (maximum - minimum)) * (chartBottom - PADDING.top);
  const baseline = scaleY(0);
  return (
    <>
      {TICK_FRACTIONS.map((fraction) => {
        const value = minimum + fraction * (maximum - minimum);
        return (
          <g key={fraction}>
            <line
              x1={PADDING.left}
              x2={WIDTH - PADDING.right}
              y1={scaleY(value)}
              y2={scaleY(value)}
              className="intelligent-ui-grid"
            />
            <text
              x={PADDING.left - 8}
              y={scaleY(value) + 4}
              textAnchor="end"
              className="intelligent-ui-tick"
            >
              {value.toLocaleString("zh-Hans-CN", { maximumFractionDigits: 2 })}
            </text>
          </g>
        );
      })}
      {config.chart_type === "bar"
        ? series.map((item, index) => (
            <BarSeries
              key={item.label}
              values={item.values}
              colour={SERIES_COLOURS[index % SERIES_COLOURS.length]}
              start={PADDING.left + index * ((slot * 0.7) / series.length)}
              slot={slot}
              barWidth={(slot * 0.7) / series.length}
              scaleY={scaleY}
              baseline={baseline}
            />
          ))
        : series.map((item, index) => (
            <LineSeries
              key={item.label}
              values={item.values}
              colour={SERIES_COLOURS[index % SERIES_COLOURS.length]}
              slot={slot}
              scaleY={scaleY}
              baseline={baseline}
              filled={config.chart_type === "area"}
            />
          ))}
      {categories.map((category, index) => (
        <text
          key={`${category}-${index}`}
          x={PADDING.left + index * slot + slot / 2}
          y={chartBottom + 18}
          textAnchor="middle"
          className="intelligent-ui-tick"
        >
          {shortLabel(category)}
        </text>
      ))}
      {config.x_label ? (
        <text
          x={(PADDING.left + WIDTH - PADDING.right) / 2}
          y={HEIGHT - 6}
          textAnchor="middle"
          className="intelligent-ui-axis-title"
        >
          {config.x_label}
        </text>
      ) : null}
    </>
  );
}

function BarSeries({
  values,
  colour,
  start,
  slot,
  barWidth,
  scaleY,
  baseline,
}: {
  values: (number | null)[];
  colour: string;
  start: number;
  slot: number;
  barWidth: number;
  scaleY: (value: number) => number;
  baseline: number;
}) {
  return (
    <>
      {values.map((value, index) =>
        value === null ? null : (
          <rect
            key={index}
            x={start + index * slot}
            y={Math.min(scaleY(value), baseline)}
            width={Math.max(barWidth - 2, 1)}
            height={Math.abs(baseline - scaleY(value))}
            fill={colour}
            rx="3"
          />
        ),
      )}
    </>
  );
}

function LineSeries({
  values,
  colour,
  slot,
  scaleY,
  baseline,
  filled,
}: {
  values: (number | null)[];
  colour: string;
  slot: number;
  scaleY: (value: number) => number;
  baseline: number;
  filled: boolean;
}) {
  // Missing samples break a line; connecting across them invents a trend.
  const segments: { x: number; y: number }[][] = [];
  let current: { x: number; y: number }[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length) segments.push(current);
      current = [];
    } else
      current.push({
        x: PADDING.left + index * slot + slot / 2,
        y: scaleY(value),
      });
  });
  if (current.length) segments.push(current);
  return (
    <>
      {segments.map((segment, index) => {
        const points = segment.map(({ x, y }) => `${x},${y}`).join(" ");
        return (
          <g key={index}>
            {filled && segment.length > 1 ? (
              <polygon
                points={`${segment[0].x},${baseline} ${points} ${segment.at(-1)!.x},${baseline}`}
                fill={colour}
                opacity={0.18}
              />
            ) : null}
            <polyline
              points={points}
              fill="none"
              stroke={colour}
              strokeWidth="2"
            />
            {segment.map(({ x, y }, i) => (
              <circle key={i} cx={x} cy={y} r="2.5" fill={colour} />
            ))}
          </g>
        );
      })}
    </>
  );
}

function PieSlices({
  values,
  categories,
  unit,
}: {
  values: (number | null)[];
  categories: string[];
  unit: string;
}) {
  const sizes = values.map((value) => value ?? 0);
  const total = sizes.reduce((sum, value) => sum + value, 0);
  const radius = Math.min(WIDTH, HEIGHT) / 2 - PADDING.top - 8;
  const centreX = 130;
  const centreY = HEIGHT / 2;
  let angle = -Math.PI / 2;
  return (
    <>
      {sizes.map((size, index) => {
        const sweep = total > 0 ? (size / total) * Math.PI * 2 : 0;
        const from = angle;
        angle += sweep;
        if (sweep >= Math.PI * 2 - 1e-9)
          return (
            <circle
              key={index}
              cx={centreX}
              cy={centreY}
              r={radius}
              fill={SERIES_COLOURS[index % SERIES_COLOURS.length]}
            />
          );
        return (
          <path
            key={index}
            d={
              total > 0 ? slicePath(centreX, centreY, radius, from, sweep) : ""
            }
            fill={SERIES_COLOURS[index % SERIES_COLOURS.length]}
            opacity={0.85}
          />
        );
      })}
      {values.map((value, index) => (
        <g
          key={index}
          transform={`translate(${centreX + radius + 28}, ${PADDING.top + index * 24})`}
        >
          <rect
            width="10"
            height="10"
            fill={SERIES_COLOURS[index % SERIES_COLOURS.length]}
          />
          <text x="16" y="9" className="intelligent-ui-legend-text">
            {shortLabel(categories[index], 12)} ·{" "}
            {formatNumber(cellText(value), unit)}
          </text>
        </g>
      ))}
    </>
  );
}

function slicePath(
  x: number,
  y: number,
  radius: number,
  from: number,
  sweep: number,
): string {
  const x1 = x + radius * Math.cos(from);
  const y1 = y + radius * Math.sin(from);
  const x2 = x + radius * Math.cos(from + sweep);
  const y2 = y + radius * Math.sin(from + sweep);
  return `M ${x} ${y} L ${x1} ${y1} A ${radius} ${radius} 0 ${sweep > Math.PI ? 1 : 0} 1 ${x2} ${y2} Z`;
}

function niceBound(value: number): number {
  if (value <= 0) return 0;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / magnitude) * magnitude;
}

function shortLabel(value: string, maximum = 10): string {
  return value.length > maximum ? `${value.slice(0, maximum - 1)}…` : value;
}
