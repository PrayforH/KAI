import { redirect } from "next/navigation";

/** Projects no longer own a page; their tasks run in the main conversation. */
export default function ProjectPage() {
  redirect("/");
}
