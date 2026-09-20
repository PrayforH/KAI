import { redirect } from "next/navigation";
export default async function Page({ params }: { params: Promise<{ agentName: string }> }) {
  const { agentName } = await params;
  redirect(`/studio/agents/${encodeURIComponent(agentName)}?section=experiments`);
}
