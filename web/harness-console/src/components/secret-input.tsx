"use client";

import { type InputHTMLAttributes, useState } from "react";

type SecretInputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & {
  revealLabel?: string;
};

export function SecretInput({
  revealLabel = "敏感内容",
  ...props
}: SecretInputProps) {
  const [visible, setVisible] = useState(false);

  return (
    <span className="secret-input">
      <input {...props} type={visible ? "text" : "password"} />
      <button
        aria-label={`${visible ? "隐藏" : "显示"}${revealLabel}`}
        aria-pressed={visible}
        className="secret-input-toggle"
        onClick={() => setVisible((current) => !current)}
        type="button"
      >
        <svg viewBox="0 0 20 20" aria-hidden="true">
          <path d="M2.5 10s2.7-4.5 7.5-4.5 7.5 4.5 7.5 4.5-2.7 4.5-7.5 4.5S2.5 10 2.5 10Z" />
          <circle cx="10" cy="10" r="2.2" />
          {visible ? null : <path d="m3.5 3.5 13 13" />}
        </svg>
      </button>
    </span>
  );
}
