import type { Metadata } from "next";
import Home from "../page";
import { PersonalAssistantEntry } from "../../components/personal-assistant-context";

export const metadata: Metadata = { title: "我的助理" };
export default function AssistantPage() {
  return <PersonalAssistantEntry><Home /></PersonalAssistantEntry>;
}
