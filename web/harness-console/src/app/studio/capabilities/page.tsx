import type { Metadata } from "next";
import { McpCatalogControlPlane } from "../../../components/agent-studio/mcp-catalog-control-plane";

export const metadata: Metadata = {
  title: "MCP 服务器",
  description: "注册、授权、停用与删除平台 MCP 服务器。",
};

/**
 * MCP management has its own page: an agent binds the servers it may use from
 * its own panel, while registering, authorising and retiring one is a platform
 * task and does not belong in an agent's drawer. The page is deliberately not in
 * the workspace navigation; agents link to it.
 */
export default function StudioCapabilitiesPage() {
  return <McpCatalogControlPlane />;
}
