import type { Metadata } from "next";
import { AgentOperationsWorkspace } from "../../../../../components/agent-studio/agent-operations-workspace";

export const metadata: Metadata = {
  title: "Evaluate & Operate",
  description: "Agent 评测、环境、部署和触发器运行控制面。",
};

export default async function AgentOperationsPage({ params }: { params: Promise<{ agentName: string }> }) {
  const { agentName } = await params;
  return <AgentOperationsWorkspace agentName={decodeURIComponent(agentName)} />;
}
