import type { ReactNode } from "react";

const shapes = {
  model: <><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4m6-4v4M9 18v4m6-4v4M2 9h4m-4 6h4m12-6h4m-4 6h4" /><rect x="10" y="10" width="4" height="4" rx=".5" /></>,
  file: <><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6m-6 4h6" /></>,
  tools: <><path d="M14 4a5 5 0 0 0-6 6L3 15l6 6 5-5a5 5 0 0 0 6-6l-4 2-4-4z" /></>,
  mcp: <><path d="m8 3 4 4m3-5 4 4M7 8l9 9m-5-13 9 9M5 12l7 7M3 21l4-4M7 8l-3 3 9 9 3-3M11 4l-4 4m13 5-4 4" /></>,
  code: <><path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-13-2 16" /></>,
  skill: <><path d="m2 9 10-5 10 5-10 5zM6 11v6q6 5 12 0v-6m4-2v8" /></>,
  knowledge: <><path d="M3 5h6l3 3h9v12H3z" /></>,
  knowledgeOpen: <><path d="M3 6h5.5l2 2H21v4" /><path d="M2.5 12h18.5l-2 8H2z" /></>,
  agent: <><rect x="4" y="7" width="16" height="14" rx="3" /><path d="M12 3v4M8 16h8" /><circle cx="8" cy="12" r=".75" /><circle cx="16" cy="12" r=".75" /></>,
  settings: <><path d="M3 6h3m4 0h11M3 12h11m4 0h3M3 18h3m4 0h11" /><circle cx="8" cy="6" r="2" /><circle cx="16" cy="12" r="2" /><circle cx="8" cy="18" r="2" /></>,
  chevron: <path d="m9 5 7 7-7 7" />,
  plus: <path d="M12 4v16M4 12h16" />,
  history: <><path d="M3 10a9 9 0 1 1 2 8M3 4v6h6m3-4v6l4 2" /></>,
} satisfies Record<string, ReactNode>;

export function ConfigurationIcon({ name, className }: { name: keyof typeof shapes; className?: string }) {
  return <svg className={className} viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]}</svg>;
}
