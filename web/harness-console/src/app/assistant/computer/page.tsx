import type { Metadata } from "next";
import { ComputerDesktop } from "../../../components/computer-desktop";

export const metadata: Metadata = { title: "我的电脑" };

export default function ComputerPage() {
  return <ComputerDesktop />;
}
