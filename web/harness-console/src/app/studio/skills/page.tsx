import type { Metadata } from "next";
import { StudioCapabilityManager } from "../../../components/agent-studio/studio-capability-manager";

export const metadata: Metadata = {
  title: "技能",
  description: "领域 Skills 与 MCP 连接管理。",
};

export default function StudioSkillsPage() {
  return <StudioCapabilityManager defaultTab="skills" />;
}
