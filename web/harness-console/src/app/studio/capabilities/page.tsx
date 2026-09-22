import { redirect } from "next/navigation";

/**
 * MCP is no longer a top-level entity in the workspace: a platform MCP is a
 * registered capability that an agent opts into, so it is managed from the
 * agent's own Tools group. The old route stays as a redirect so bookmarks and
 * hand-written links keep working.
 */
export default function StudioCapabilitiesPage() {
  redirect("/studio/skills");
}
