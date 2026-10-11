"use client";

import { useState } from "react";

import type { FormField } from "./schema";

export type FieldValues = Record<string, string[]>;

export function emptyValues(fields: readonly FormField[]): FieldValues {
  return Object.fromEntries(fields.map((field) => [field.key, []]));
}

/** A field's answer as one line of the message the form sends. Every answer is a
 * string: the platform turns the message into a user turn, not into typed data,
 * so there is nothing here to coerce or trust. */
export function formatValues(fields: readonly FormField[], values: FieldValues): string | null {
  const lines: string[] = [];
  for (const field of fields) {
    const filled = (values[field.key] ?? []).map((value) => value.trim()).filter(Boolean);
    if (!filled.length) {
      if (field.required) return null;
      continue;
    }
    lines.push(`${field.label}：${filled.join("、")}`);
  }
  return lines.length ? lines.join("\n") : null;
}

/** A form gathers the fields an answer is missing and submits them as the next
 * user turn. Nothing is written from the browser: the submit is a message. */
export function FormView({
  fields,
  submitLabel,
  disabled,
  onSubmit,
}: {
  fields: readonly FormField[];
  submitLabel: string;
  disabled: boolean;
  /** Absent outside a turn: the fields are then shown read-only with no submit,
   * because there is nowhere for the answer to go. */
  onSubmit?: (message: string) => void | Promise<void>;
}) {
  const [values, setValues] = useState<FieldValues>(() => emptyValues(fields));
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [error, setError] = useState("");
  const answer = formatValues(fields, values);
  const locked = disabled || !onSubmit || state === "sending" || state === "sent";

  const set = (key: string, next: string[]) =>
    setValues((current) => ({ ...current, [key]: next }));

  async function submit() {
    if (locked || !answer || !onSubmit) return;
    setState("sending");
    setError("");
    try {
      await onSubmit(answer);
      setState("sent");
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
                <div className="intelligent-ui-options" role="group" aria-label={field.label}>
                  {field.options.map((option) => (
                    <label key={option}>
                      <input
                        type={field.type === "multi" ? "checkbox" : "radio"}
                        name={field.key}
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
                  type={field.type === "number" ? "number" : field.type === "date" ? "date" : "text"}
                  aria-label={field.label}
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
      {error ? <p role="alert" className="intelligent-ui-form-error">{error}</p> : null}
      {state === "sent" || !onSubmit ? null : (
        <div className="intelligent-ui-toolbar intelligent-ui-form-footer">
          <button type="button" disabled={locked || !answer} onClick={() => void submit()}>
            {state === "sending" ? "提交中…" : submitLabel}
          </button>
        </div>
      )}
    </div>
  );
}
