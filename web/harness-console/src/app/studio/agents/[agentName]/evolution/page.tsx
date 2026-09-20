import { EvolutionWorkspace } from "../../../../../components/agent-studio/evolution-workspace";
export const metadata = { title: "持续改进", description: "基于固定证据评估和审核智能体改进。" };
export default async function Page({ params }: { params: Promise<{ agentName: string }> }) {
  const { agentName } = await params;
  return <EvolutionWorkspace agentName={decodeURIComponent(agentName)} />;
}
