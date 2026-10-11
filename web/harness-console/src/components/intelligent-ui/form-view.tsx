"use client";

import { useId, useState } from "react";

import type { FormField } from "./schema";

export type FieldValues = Record<string, string[]>;

export function emptyValues(fields: readonly FormField[]): FieldValues {
  return Object.fromEntries(fields.map((field) => [field.key, []]));
}

/** Human-readable confirmation; transport retains the original field keys. */
export function formatValues(
  fields: readonly FormField[],
  values: FieldValues,
): string | null {
  const lines: string[] = [];
  for (const field of fields) {
    const filled = (values[field.key] ?? [])
      .map((value) => value.trim())
      .filter(Boolean);
    if (!filled.length) {
      if (field.required) return null;
      continue;
    }
    lines.push(`${field.label}：${filled.join("、")}`);
  }
  return lines.length
    ? lines.join("\n")
    : fields.some((field) => field.required)
      ? null
      : "无补充内容";
}

/** Admission is separate from execution; the host displays durable status. */
export function FormView({
  fields,
  submitLabel,
  disabled,
  onSubmit,
  values: savedValues,
  onChange,
  singleUse = true,
}: {
  fields: readonly FormField[];
  submitLabel: string;
  disabled: boolean;
  /** Absent outside a turn: the fields are then shown read-only with no submit,
   * because there is nowhere for the answer to go. */
  onSubmit?: (values: FieldValues) => void | Promise<void>;
  values?: FieldValues;
  onChange?: (values: FieldValues) => void;
  singleUse?: boolean;
}) {
  const [localValues, setValues] = useState<FieldValues>(() =>
    emptyValues(fields),
  );
  const values = savedValues ?? localValues;
  const group = useId();
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">(
    "idle",
  );
  const [error, setError] = useState("");
  const answer = formatValues(fields, values);
  const locked =
    disabled || !onSubmit || state === "sending" || state === "sent";

  const set = (key: string, next: string[]) => {
    const changed = { ...values, [key]: next };
    setValues(changed);
    onChange?.(changed);
  };

  async function submit() {
    if (locked || !answer || !onSubmit) return;
    setState("sending");
    setError("");
    try {
      await onSubmit(values);
      setState(singleUse ? "sent" : "idle");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "发送失败，请重试");
      setState("error");
    }
  }

  return (
    <div className="intelligent-ui-block intelligent-ui-form">
      <div className="intelligent-ui-fields">
        {fields.map((field) => {
          const filled = values[field.key] ?? [];
          return (
            <div className="intelligent-ui-field" key={field.key}>
              <span className="intelligent-ui-field-label">
                {field.label}
                {field.required ? <em aria-hidden="true">*</em> : null}
              </span>
              {field.options.length > 0 ? (
                <div
                  className="intelligent-ui-options"
                  role="group"
                  aria-label={field.label}
                >
                  {field.options.map((option) => (
                    <label key={option}>
                      <input
                        type={field.type === "multi" ? "checkbox" : "radio"}
                        name={`${group}-${field.key}`}
                        checked={filled.includes(option)}
                        disabled={locked}
                        onChange={(event) =>
                          set(
                            field.key,
                            field.type === "single"
                              ? [option]
                              : event.target.checked
                                ? [...filled, option]
                                : filled.filter((item) => item !== option),
                          )
                        }
                      />
                      <span>{option}</span>
                    </label>
                  ))}
                </div>
              ) : (
                <input
                  className="intelligent-ui-input"
                  type={
                    field.type === "number"
                      ? "number"
                      : field.type === "date"
                        ? "date"
                        : "text"
                  }
                  aria-label={field.label}
                  aria-required={field.required}
                  placeholder={field.placeholder || "填写"}
                  maxLength={600}
                  disabled={locked}
                  value={filled[0] ?? ""}
                  onChange={(event) => set(field.key, [event.target.value])}
                />
              )}
            </div>
          );
        })}
      </div>
      {onSubmit && answer ? (
        <details className="intelligent-ui-note">
          <summary>确认提交内容</summary>
          <p style={{ whiteSpace: "pre-wrap" }}>{answer}</p>
        </details>
      ) : null}
      {error ? (
        <p role="alert" className="intelligent-ui-form-error">
          {error}
        </p>
      ) : null}
      {state === "sent" || !onSubmit ? null : (
        <div className="intelligent-ui-toolbar intelligent-ui-form-footer">
          <button
            type="button"
            disabled={locked || !answer}
            onClick={() => void submit()}
          >
            {state === "sending" ? "提交中…" : submitLabel}
          </button>
        </div>
      )}
    </div>
  );
}
