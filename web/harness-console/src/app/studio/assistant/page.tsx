import type { Metadata } from "next";
import { AssistantManager } from "../../../components/agent-studio/assistant-manager";
export const metadata: Metadata = { title: "我的助理" };
export default function AssistantPage() { return <AssistantManager />; }
