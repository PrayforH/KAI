"use client";

import { CodexResponseIcon } from "../codex-response-icon";
import { uiPath } from "./client";
import type { Presentation } from "./schema";

/** Keep document actions directly available in both inline and expanded views. */
export function PresentationMenu({ presentation }: { presentation: Presentation }) {
  const url = `${uiPath(presentation.presentation_id)}/html?revision=${presentation.revision}`;
  const artifactId = presentation.data.artifact_id;
  const name = typeof presentation.data.name === "string" ? presentation.data.name : "document.html";
  return <>
    <a className="intelligent-ui-document-action" href={url} target="_blank" rel="noopener noreferrer"
      aria-label="在新标签页打开" title="在新标签页打开"><CodexResponseIcon name="external" /></a>
    {typeof artifactId === "string" && <a className="intelligent-ui-document-action"
      href={`/api/harness/artifacts/${encodeURIComponent(artifactId)}`} download={name}
      aria-label="下载 HTML" title="下载 HTML"><CodexResponseIcon name="download" /></a>}
  </>;
}
