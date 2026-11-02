import { redirect } from "next/navigation";

/** Existing bookmarks return to the assistant; work and history remain intact. */
export default function RetiredAssistantManagerPage() {
  redirect("/assistant");
}
