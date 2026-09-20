import type { Metadata } from "next";
import { AutomationManager } from "../../../components/agent-studio/automation-manager";

export const metadata: Metadata = { title: "自动化任务" };

export default function StudioAutomationPage() {
  return <AutomationManager />;
}
