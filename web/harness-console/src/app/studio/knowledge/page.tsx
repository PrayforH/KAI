import type { Metadata } from "next";
import { McpCatalogControlPlane } from "../../../components/agent-studio/mcp-catalog-control-plane";

export const metadata: Metadata = { title: "知识库" };

export default function KnowledgePage() {
  return <McpCatalogControlPlane mode="knowledge" />;
}
