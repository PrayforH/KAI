"use client";

import { useRouter } from "next/navigation";
import { StudioPageHeaderLinks } from "./studio-page-header";

export type StudioSection = "skills";

const items: Array<{ id: StudioSection; href: string; label: string }> = [
  { id: "skills", href: "/studio/skills", label: "技能" },
];

/**
 * The 技能 section header. It keeps the same tab strip placement as the
 * automation page; MCP is configured from the agent that uses it, not here.
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
