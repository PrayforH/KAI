"use client";

import { useEffect, useMemo, useState } from "react";
import { writeTextToClipboard } from "../../lib/clipboard";

import { type ChartRow, cellText, formatNumber, isNumeric } from "./cells";
import { downloadCsv, toCsv } from "./csv";
import type { PresentationCell } from "./schema";

export type TableColumn = {
  key: string;
  label?: string | null;
  align?: "left" | "right" | "center" | null;
};

export type TableRow = Record<string, PresentationCell>;

/** A comparable record set. Sorting is local: it reorganises data the server
 * already sent and never asks the platform to re-run anything. */
export function TableView({
  columns,
  rows,
  title,
  caption,
  sort: savedSort,
  onSort,
}: {
  columns: TableColumn[];
  rows: TableRow[];
  title?: string | null;
  caption?: string;
  sort?: { key: string; descending: boolean } | null;
  onSort?: (sort: { key: string; descending: boolean }) => void;
}) {
  const [localSort, setSort] = useState<{
    key: string;
    descending: boolean;
  } | null>(null);
  const sort = savedSort === undefined ? localSort : savedSort;
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const ordered = useMemo(
    () =>
      sort
        ? [...rows].sort((left, right) => {
            const a = left[sort.key];
            const b = right[sort.key];
            const result =
              isNumeric(a) && isNumeric(b)
                ? a - b
                : cellText(a).localeCompare(cellText(b), "zh-Hans-CN", {
                    numeric: true,
                  });
            return sort.descending ? -result : result;
          })
        : rows,
    [rows, sort],
  );

  const header = columns.map((column) => column.label ?? column.key);
  const body = ordered.map((row) =>
    columns.map((column) => cellText(row[column.key])),
  );
  const numericColumn = (key: string) =>
    rows.some((row) => isNumeric(row[key]));
  const alignFor = (column: TableColumn) =>
    column.align ?? (numericColumn(column.key) ? "right" : "left");

  const toggleSort = (key: string) => {
    const next = {
      key,
      descending: sort?.key === key ? !sort.descending : true,
    };
    setSort(next);
    onSort?.(next);
  };

  const copy = async () => {
    const success = await writeTextToClipboard(
      [header.join("\t"), ...body.map((row) => row.join("\t"))].join("\n"),
    );
    setCopied(success);
    setCopyError(success ? "" : "复制失败，请下载 CSV 或选中表格复制。");
  };

  return (
    <div className="intelligent-ui-block intelligent-ui-table">
      <div className="intelligent-ui-toolbar">
        <span className="intelligent-ui-count">
          {rows.length} 行{caption ? ` · ${caption}` : ""}
        </span>
        <span className="intelligent-ui-toolbar-actions">
          <button type="button" onClick={() => void copy()}>
            {copied ? "已复制" : "复制"}
          </button>
          <button
            type="button"
            onClick={() =>
              downloadCsv(`${title ?? "数据"}.csv`, toCsv(header, body))
            }
          >
            下载 CSV
          </button>
        </span>
      </div>
      {copyError ? (
        <p role="alert" className="intelligent-ui-note">
          {copyError}
        </p>
      ) : null}
      <div className="intelligent-ui-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((column, index) => (
                <th
                  key={column.key}
                  data-align={alignFor(column)}
                  aria-sort={
                    sort?.key === column.key
                      ? sort.descending
                        ? "descending"
                        : "ascending"
                      : "none"
                  }
                >
                  <button type="button" onClick={() => toggleSort(column.key)}>
                    {header[index]}
                    <span aria-hidden="true">
                      {sort?.key === column.key
                        ? sort.descending
                          ? " ↓"
                          : " ↑"
                        : ""}
                    </span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {body.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td
                    key={columns[cellIndex].key}
                    data-align={alignFor(columns[cellIndex])}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** The numbers a chart was drawn from, so a reader can verify instead of
 * trusting the bars. */
export function ChartDataTable({
  rows,
  xField,
  yFields,
  yLabels,
  unit,
  xLabel,
}: {
  rows: ChartRow[];
  xField: string;
  yFields: string[];
  yLabels?: string[];
  unit?: string;
  xLabel?: string;
}) {
  return (
    <div className="intelligent-ui-scroll">
      <table>
        <thead>
          <tr>
            <th>{xLabel || xField}</th>
            {yFields.map((field, index) => (
              <th key={field} data-align="right">
                {yLabels?.[index] ?? field}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              <td>{cellText(row[xField])}</td>
              {yFields.map((field) => (
                <td key={field} data-align="right">
                  {formatNumber(cellText(row[field]), unit)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
