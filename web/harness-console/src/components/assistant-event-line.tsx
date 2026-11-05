"use client";
import Link from "next/link";
import { BotRailAvatar, botAccent } from "./bot-rail";
import { assistantCopy } from "../lib/assistant-copy";

/** Shared visual grammar for incoming results and outgoing delegations. */
export function AssistantEventLine({label, name, href, botId, color, avatar, attention = false}: {
  label: string; name: string; href: string; botId?: string;
  color?: string; avatar?: string; attention?: boolean;
}) {
  const tone = color && /^#[0-9a-f]{6}$/i.test(color) ? color : botId ? botAccent(botId) : "#658b7d";
  return <div className="assistant-event-line" role={attention ? "alert" : undefined}>
    <span>{label}</span>
    <Link href={href} scroll={false}>
      <BotRailAvatar tone={tone} shape={avatar} />
      <span>{assistantCopy(name) || "子助理"}</span>
    </Link>
  </div>;
}
