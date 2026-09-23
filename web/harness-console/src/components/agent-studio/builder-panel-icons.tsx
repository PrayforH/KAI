export function PanelExpandIcon({ expanded = false }: { expanded?: boolean }) {
  return <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={expanded ? "M8 3v5H3m9 9v-5h5M8 8 3 3m9 9 5 5" : "M3 8V3h5m9 9v5h-5M3 3l5 5m9 9-5-5"}/></svg>;
}

export function BuilderFilesIcon() {
  return <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M11 3H5a1 1 0 0 0-1 1v12a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V8z"/><path d="M11 3v5h5M7 11h6m-6 3h4"/></svg>;
}

export function BuilderConversationsIcon() {
  return <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M16 12a2 2 0 0 1-2 2H8l-4 3V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/><path d="M7 7h6m-6 3h4"/></svg>;
}
