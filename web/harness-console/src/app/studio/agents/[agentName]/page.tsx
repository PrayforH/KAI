import { AgentStudioWorkbench } from "../../../../components/agent-studio/agent-studio-workbench";
import { AgentWorkspace } from "../../../../components/agent-studio/agent-workspace";
import { agentSection } from "../../../../lib/agent-workspace";
export const metadata = { title: "智能体工作区", description: "构建、诊断、评测与发布，在同一个智能体工作区持续改进。" };
export default async function Page({ params, searchParams }: { params: Promise<{ agentName: string }>; searchParams: Promise<{ section?: string; draft?: string; session?: string; job?: string; evolutionJob?: string; candidate?: string; objective?: string }> }) {
  const [{ agentName }, query] = await Promise.all([params, searchParams]);
  if (query.section === "playground" || query.section === "sessions") return <AgentStudioWorkbench agentName={agentName} initialView={query.section} initialSessionId={query.session} />;
  return <AgentWorkspace agentName={agentName} section={agentSection(query.section)} draftId={query.draft} jobId={query.job} evolutionJob={query.evolutionJob} candidateId={query.candidate} objective={query.objective} />;
}
