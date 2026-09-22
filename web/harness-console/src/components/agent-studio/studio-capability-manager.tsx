"use client";

import { SkillsCatalogPage } from "./skills-catalog-page";
import { StudioSectionNavigation } from "./studio-section-navigation";
import styles from "./studio-capability-manager.module.css";

/**
 * The 技能 management zone. MCP servers are registered from the agent that uses
 * them (its Tools group), so this page no longer hosts an MCP tab.
 */
export function StudioCapabilityManager() {
  return (
    <div className={styles.manager}>
      <StudioSectionNavigation active="skills" />
      <SkillsCatalogPage />
    </div>
  );
}
