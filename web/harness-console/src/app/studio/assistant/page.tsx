import type { Metadata } from "next";
import { AssistantManager } from "../../../components/agent-studio/assistant-manager";
export const metadata: Metadata = { title: "持续事项" };
export default function AssistantPage() { return <AssistantManager />; }
