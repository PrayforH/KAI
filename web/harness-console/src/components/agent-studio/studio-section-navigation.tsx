"use client";

import { useRouter } from "next/navigation";
import { StudioPageHeaderLinks } from "./studio-page-header";

export type StudioSection = "skills" | "mcp";

const items: Array<{ id: StudioSection; href: string; label: string }> = [
  { id: "skills", href: "/studio/skills", label: "技能" },
  { id: "mcp", href: "/studio/capabilities", label: "MCP" },
];

/**
 * 技能 / MCP switch. Both routes share one page header so the section tabs sit
 * in the same place as the automation page's tabs.
 */
export function StudioSectionNavigation({ active }: { active: StudioSection }) {
  const router = useRouter();
  return (
    <StudioPageHeaderLinks
      ariaLabel="技能与 MCP 管理"
      links={items.map((item) => ({
        id: item.id,
        label: item.label,
        href: item.href,
        active: item.id === active,
      }))}
      onNavigate={(href) => router.push(href)}
    />
  );
}
