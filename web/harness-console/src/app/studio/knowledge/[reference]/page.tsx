import type { Metadata } from "next";
import { KnowledgeBaseDetail } from "../../../../components/knowledge/knowledge-base-detail";

export const metadata: Metadata = { title: "知识库详情" };

export default async function KnowledgeBaseDetailPage({
  params,
}: {
  params: Promise<{ reference: string }>;
}) {
  const { reference } = await params;
  return <KnowledgeBaseDetail reference={reference} />;
}
