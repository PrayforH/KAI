/** Shared 20-unit geometry for the drawer's navigation and utility actions. */
const paths = {
  compose: "M11.8 3.8 14 6M10.4 5.2l4.3-4.3a1.55 1.55 0 0 1 2.2 2.2l-8.2 8.2-3 .8.8-3 3.9-3.9M9 3.5H5A2.5 2.5 0 0 0 2.5 6v9A2.5 2.5 0 0 0 5 17.5h9a2.5 2.5 0 0 0 2.5-2.5v-4",
  tasks: "M5 3.5h10A2.5 2.5 0 0 1 17.5 6v8a2.5 2.5 0 0 1-2.5 2.5H5A2.5 2.5 0 0 1 2.5 14V6A2.5 2.5 0 0 1 5 3.5ZM6 7.5h8M6 11h5",
  agents: "M4.5 3h3A1.5 1.5 0 0 1 9 4.5v3A1.5 1.5 0 0 1 7.5 9h-3A1.5 1.5 0 0 1 3 7.5v-3A1.5 1.5 0 0 1 4.5 3ZM12.5 11h3a1.5 1.5 0 0 1 1.5 1.5v3a1.5 1.5 0 0 1-1.5 1.5h-3a1.5 1.5 0 0 1-1.5-1.5v-3a1.5 1.5 0 0 1 1.5-1.5ZM12 6h5m-2.5-2.5v5M3 14h5m-2.5-2.5v5",
  files: "M11.5 2.5H6A2 2 0 0 0 4 4.5v11a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7l-4.5-4.5ZM11 3v4.5h4.5M7.5 11h5m-5 3h5",
  automation: "M16.5 8a7 7 0 1 0 .3 3M17 3.5V8h-4.5M10 6v4l2.5 1.5",
  knowledge: "M10 5v12M10 5C8.2 3.5 5.5 3 2.5 3.5V15c3-.5 5.7 0 7.5 1.5 1.8-1.5 4.5-2 7.5-1.5V3.5C14.5 3 11.8 3.5 10 5Z",
  skills: "M8 3H4.5A1.5 1.5 0 0 0 3 4.5V8h1a2 2 0 1 1 0 4H3v3.5A1.5 1.5 0 0 0 4.5 17H8v-1a2 2 0 1 1 4 0v1h3.5a1.5 1.5 0 0 0 1.5-1.5V12h-1a2 2 0 1 1 0-4h1V4.5A1.5 1.5 0 0 0 15.5 3H12v1a2 2 0 1 1-4 0V3Z",
  folder: "M2.5 6A2.5 2.5 0 0 1 5 3.5h3l2 2h5A2.5 2.5 0 0 1 17.5 8v6A2.5 2.5 0 0 1 15 16.5H5A2.5 2.5 0 0 1 2.5 14V6Z",
  search: "M14 8.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0Zm-1.5 4L17 17",
  panel: "M5 3.5h10A2.5 2.5 0 0 1 17.5 6v8a2.5 2.5 0 0 1-2.5 2.5H5A2.5 2.5 0 0 1 2.5 14V6A2.5 2.5 0 0 1 5 3.5ZM7.5 3.5v13",
  plus: "M10 4v12M4 10h12",
  archive: "M3 3.5h14V7H3V3.5ZM4 7v8a1.5 1.5 0 0 0 1.5 1.5h9A1.5 1.5 0 0 0 16 15V7M8 10.5h4",
  usage: "M4 16V9m6 7V4m6 12v-6",
  spaces: "M10 6.5a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM2 17v-1a5 5 0 0 1 10 0v1M13 3.8a3 3 0 0 1 0 5.4M14 11.5a4 4 0 0 1 4 4V17",
  data: "M16.5 5a6.5 2.5 0 1 1-13 0 6.5 2.5 0 0 1 13 0ZM3.5 5v10a6.5 2.5 0 0 0 13 0V5M3.5 10a6.5 2.5 0 0 0 13 0",
  appearance: "M10 17.5a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15ZM10 2.5v15",
  settings: "M3 5h14M3 15h14M7 3v4m6 6v4M3 10h14m-6-2v4",
  logout: "M8 3H5a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h3M8 10h9m-3.5-3.5L17 10l-3.5 3.5",
  check: "m4.5 10 3.5 3.5 7.5-8",
  chevronDown: "m6 8 4 4 4-4",
  chevronUp: "m6 12 4-4 4 4",
} as const;

export function SidebarIcon({ name, className }: { name: keyof typeof paths; className?: string }) {
  return <svg className={className} data-sidebar-icon={name} viewBox="0 0 20 20" aria-hidden="true" focusable="false"
    style={{ width: 18, height: 18, flexShrink: 0, fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round", strokeLinejoin: "round" }}>
    <path d={paths[name]} />
  </svg>;
}
