import type { Metadata } from "next";
import { StudioCapabilityManager } from "../../../components/agent-studio/studio-capability-manager";

export const metadata: Metadata = {
  title: "技能",
  description: "领域 Skills 的目录、导入与审查。",
};

export default function StudioSkillsPage() {
  return <StudioCapabilityManager />;
}
