import { redirect } from "next/navigation";
export default async function Page({ params, searchParams }: { params: Promise<{ agentName: string }>; searchParams: Promise<{ evolutionJob?: string; candidate?: string }> }) {
  const [{ agentName }, query] = await Promise.all([params, searchParams]);
  const search = new URLSearchParams({ section: query.evolutionJob ? "release" : "evaluation" });
  if (query.evolutionJob) search.set("evolutionJob", query.evolutionJob);
  if (query.candidate) search.set("candidate", query.candidate);
  redirect(`/studio/agents/${encodeURIComponent(agentName)}?${search}`);
}
