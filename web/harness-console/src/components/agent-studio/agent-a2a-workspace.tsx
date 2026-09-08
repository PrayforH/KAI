"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "../auth-provider";
import {
  studioClient,
  type StudioDraftSummary,
  type StudioEnvironment,
} from "../../lib/studio-client";
import { AgentTriggerControlPlane } from "./agent-trigger-control-plane";
import styles from "./agent-a2a-workspace.module.css";
import { PRODUCT_NAME } from "../product-brand";

export function AgentA2AWorkspace({ agentName }: { agentName: string }) {
  const { membership } = useAuth();
  const [agent, setAgent] = useState<StudioDraftSummary | null>(null);
  const [environments, setEnvironments] = useState<StudioEnvironment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const canManage = membership.role === "owner" || membership.role === "admin";

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        const drafts = await studioClient.listDrafts();
        const match = drafts.find((item) => item.name === agentName);
        if (!match) throw new Error("找不到这个 Agent，可能已被删除或当前租户无权访问。");
        const nextEnvironments = await studioClient.listEnvironments(agentName);
        if (!active) return;
        setAgent(match);
        setEnvironments(nextEnvironments);
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : "A2A 控制台读取失败");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [agentName]);

  return (
    <section className={styles.workspace}>
        <header className={styles.topbar}>
          <Link href={`/studio/agents?draft=${encodeURIComponent(agent?.draftId ?? "")}&section=release`}>
            <span aria-hidden="true">←</span> 返回{PRODUCT_NAME}
          </Link>
          <span className={styles.protocolBadge}>A2A 1.0</span>
        </header>

        {loading ? (
          <section className={styles.state} aria-busy="true">
            <span>A2A</span>
            <h1>正在读取协议接入配置</h1>
            <p>加载 Agent 版本、环境和已发布入口。</p>
          </section>
        ) : error || !agent ? (
          <section className={styles.state} role="alert">
            <span>!</span>
            <h1>A2A 控制台暂不可用</h1>
            <p>{error || "未找到 Agent。"}</p>
          </section>
        ) : (
          <div className={styles.content}>
            <section className={styles.hero}>
              <div>
                <span className={styles.eyebrow}>PROTOCOL SURFACE</span>
                <h1>{agent.displayName} 的 A2A 接入</h1>
                <p>
                  让其他 Agent 通过标准 Agent Card 发现能力、提交消息，并沿用平台现有的身份、环境和运行治理。
                </p>
              </div>
              <dl>
                <div>
                  <dt>发布版本</dt>
                  <dd>{agent.publishedVersion ?? "尚未发布"}</dd>
                </div>
                <div>
                  <dt>协议边界</dt>
                  <dd>Agent Card + message:send</dd>
                </div>
                <div>
                  <dt>运行治理</dt>
                  <dd>审批、取消、制品、Trace</dd>
                </div>
              </dl>
            </section>

            <section className={styles.guide} aria-label="A2A 接入流程">
              <article>
                <span>01</span>
                <div><strong>选择环境</strong><small>只允许健康且具备工作负载身份的部署。</small></div>
              </article>
              <article>
                <span>02</span>
                <div><strong>发布 Agent Card</strong><small>机器可发现名称、能力和消息端点。</small></div>
              </article>
              <article>
                <span>03</span>
                <div><strong>分发访问密钥</strong><small>密钥只显示一次，可随时轮换或停用入口。</small></div>
              </article>
            </section>

            <AgentTriggerControlPlane
              agentName={agent.name}
              publishedVersion={agent.publishedVersion}
              environments={environments}
              canManage={canManage}
              kindFilter="a2a"
            />
          </div>
        )}
    </section>
  );
}
