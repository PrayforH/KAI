"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "../auth-provider";
import { studioClient, type StudioEnvironment } from "../../lib/studio-client";
import { AgentTriggerControlPlane } from "./agent-trigger-control-plane";
export function AgentAutomation({ agentName, version }: { agentName: string; version: string | null }) {
  const { membership } = useAuth();
  const [environments, setEnvironments] = useState<StudioEnvironment[]>();
  const [error, setError] = useState("");
  const [revision, refresh] = useState(0);
  useEffect(() => {
    let active = true; setError(""); setEnvironments(undefined);
    void studioClient.listEnvironments(agentName).then(items => { if (active) setEnvironments(items); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [agentName, revision]);
  return <section><p>Cron、Webhook 和应用事件调用此智能体的环境部署版本。先完成评测与部署，再启用自动运行；调整个人默认版本不会自动更改环境路由。</p><Link href={`/studio/agents/${encodeURIComponent(agentName)}?section=release`}>查看部署版本与策略 →</Link>{error ? <p role="alert">{error} <button onClick={() => refresh(v => v + 1)}>重试</button></p> : !environments ? <p role="status">正在读取可运行环境…</p> : <AgentTriggerControlPlane agentName={agentName} publishedVersion={version} environments={environments} canManage={membership.role === "owner" || membership.role === "admin"} />}</section>;
}
