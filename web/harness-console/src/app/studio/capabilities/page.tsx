import type { Metadata } from "next";
import { StudioCapabilityManager } from "../../../components/agent-studio/studio-capability-manager";

export const metadata: Metadata = { title: "MCP 能力目录" };

export default function StudioCapabilitiesPage() {
  return <StudioCapabilityManager defaultTab="mcp" />;
}
