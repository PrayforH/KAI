import type { Metadata } from "next";
import { AgentA2AWorkspace } from "../../../../../components/agent-studio/agent-a2a-workspace";

export const metadata: Metadata = {
  title: "A2A 接入",
  description: "发布并管理 Agent 的 A2A 1.0 协议入口。",
};

export default async function AgentA2APage({
  params,
}: {
  params: Promise<{ agentName: string }>;
}) {
  const { agentName } = await params;
  return <AgentA2AWorkspace agentName={agentName} />;
}
