"use client";

import { SkillsCatalogPage } from "./skills-catalog-page";
import { McpCatalogControlPlane } from "./mcp-catalog-control-plane";
import { StudioSectionNavigation } from "./studio-section-navigation";
import styles from "./studio-capability-manager.module.css";

/**
 * The unified 技能 / MCP management zone. `/studio/capabilities` opens with the
 * MCP tab, `/studio/skills` with the 技能 tab. Both share one left sidebar and
 * swap only the center content.
 */
export function StudioCapabilityManager({
  defaultTab,
}: {
  defaultTab: "skills" | "mcp";
}) {
  const isSkills = defaultTab === "skills";
  return (
    <div className={styles.manager}>
      <StudioSectionNavigation active={isSkills ? "skills" : "mcp"} />
      {isSkills ? <SkillsCatalogPage /> : <McpCatalogControlPlane />}
    </div>
  );
}
