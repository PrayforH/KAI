import type { ReactNode } from "react";

/** Preserve the original answer and evidence behind an explicit native disclosure. */
export function AssistantControlDetails({ children }: { children: ReactNode }) {
  return (
    <details className="assistant-control-details">
      <summary>查看完整回复与依据</summary>
      {children}
    </details>
  );
}
