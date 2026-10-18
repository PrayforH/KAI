import { redirect } from "next/navigation";
import { projectHref, projectTab } from "../../../../lib/project-space";

export default async function ProjectPage({ params, searchParams }: {
  params: Promise<{ projectId: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const [{ projectId }, query] = await Promise.all([params, searchParams]);
  redirect(projectHref(projectId, projectTab(query.tab)));
}
