import type { Metadata } from "next";
import { KnowledgeConsole } from "../../../components/knowledge/knowledge-console";

export const metadata: Metadata = { title: "知识库" };

export default function KnowledgePage() {
  return <KnowledgeConsole />;
}
