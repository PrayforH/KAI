import type { Metadata } from "next";
import { MyFiles } from "../../../components/my-files/my-files";

export const metadata: Metadata = { title: "我的文件" };

export default function MyFilesPage() {
  return <MyFiles />;
}
