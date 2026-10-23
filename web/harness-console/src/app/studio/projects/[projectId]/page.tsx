import { ProjectHome } from "../../../../components/project-home";
import { projectTab } from "../../../../lib/project-space";

export const metadata = { title: "项目空间", description: "整理项目资料、推进任务并归集成果。任务在主对话中执行。" };

export default async function ProjectPage({ params, searchParams }: {
  params: Promise<{ projectId: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const [{ projectId }, query] = await Promise.all([params, searchParams]);
  return <ProjectHome key={projectId} projectId={projectId} initialTab={projectTab(query.tab)} />;
}
