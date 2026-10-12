import type { ComponentPropsWithoutRef } from "react";
import { CodexResponseIcon } from "./codex-response-icon";

type SourceLinkProps = ComponentPropsWithoutRef<"a">;

function externalHost(href: string | undefined) {
  if (!href || !/^https?:\/\//i.test(href)) {
    return null;
  }

  try {
    return new URL(href).hostname;
  } catch {
    return null;
  }
}

export function SourceLink({ children, className, href, ...props }: SourceLinkProps) {
  const host = externalHost(href);

  if (!host) {
    return (
      <a className={className} href={href} {...props}>
        {children}
      </a>
    );
  }

  return (
    <a
      className={["source-link", className].filter(Boolean).join(" ")}
      href={href}
      {...props}
      target="_blank"
      rel="noopener noreferrer"
    >
      <span className="source-title">{children}</span>
      <span className="source-host">{host}</span>
      <span className="source-open" aria-hidden="true">
        <CodexResponseIcon name="external" />
      </span>
    </a>
  );
}
