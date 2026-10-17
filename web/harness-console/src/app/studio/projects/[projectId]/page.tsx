import type { Metadata } from "next";
import { ProjectHome } from "../../../../components/project-home";

export const metadata: Metadata = { title: "项目" };

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <ProjectHome projectId={projectId} />;
}
