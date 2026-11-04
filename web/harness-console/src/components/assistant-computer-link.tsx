"use client";

export function AssistantComputerLink({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  async function toggle() {
    if (window.location.protocol === "https:") { onToggle(); return; }
    // Resolve a secure viewer origin only after the user opens the computer.
    try {
      const response = await fetch("/api/computer/status", { cache: "no-store" });
      if (!response.ok) return;
      const status = await response.json() as { desktop_url?: string };
      const url = new URL(status.desktop_url ?? "/assistant/computer", window.location.href);
      if (url.hostname !== window.location.hostname || url.protocol !== "https:") return;
      url.pathname = "/assistant"; url.search = "?computer=open";
      window.location.assign(url.href);
    } catch { /* The conversation remains usable if the viewer is unavailable. */ }
  }
  return <button type="button" className="header-icon-button" aria-label={open ? "收起电脑" : "打开电脑"} title={open ? "收起电脑" : "打开电脑"} aria-expanded={open} aria-controls="assistant-computer-pane" onClick={() => void toggle()}>
    <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3" width="15" height="10" rx="1.5" /><path d="M7 17h6m-3-4v4" /></svg>
  </button>;
}
