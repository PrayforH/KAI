import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const provider = readFileSync(new URL("../AppServerProvider.tsx", import.meta.url), "utf8");
const newChatPage = readFileSync(new URL("../../app/chat/page.tsx", import.meta.url), "utf8");
const historyPage = readFileSync(new URL("../../app/chat/[id]/page.tsx", import.meta.url), "utf8");
const chatView = readFileSync(new URL("../../components/chat/ChatView.tsx", import.meta.url), "utf8");
const splitColumn = readFileSync(new URL("../../components/layout/SplitColumn.tsx", import.meta.url), "utf8");
const sideChatPanel = readFileSync(new URL("../../components/layout/WorkspaceSidebar/SideChatPanel.tsx", import.meta.url), "utf8");
const appShell = readFileSync(new URL("../../components/layout/AppShell.tsx", import.meta.url), "utf8");

describe("app-server 断线重连接线", () => {
  it("连接关闭后保留运行 Turn 并进入自动重连", () => {
    expect(provider).toContain('connection: { source: "web-bridge", data: "reconnecting" }');
    expect(provider).toContain("scheduleReconnect()");
    expect(provider).toContain("reconnectDelayMs(reconnectAttempt)");
    expect(provider).not.toContain("failRunningTurnOnTransportClose(current.activeTurn");
  });

  it("Web 登录失效时停止重连并返回原页面", () => {
    expect(provider).toContain("error instanceof BridgeAuthenticationError");
    expect(provider).toContain("window.location.replace(`/login?reason=session-expired&next=${encodeURIComponent(next)}`)");
  });

  it("每次 bootstrap 先解析最新 bridge URL，再复用同一个 client 连接", () => {
    expect(provider.match(/new AppServerBrowserClient/g)).toHaveLength(1);
    expect(provider).toContain("const latestBridgeUrl = await resolveCodexBridgeUrl(");
    expect(provider).toContain("abortController.signal");
    expect(provider).toContain("await client.connect(latestBridgeUrl)");
    expect(provider.indexOf("const latestBridgeUrl = await resolveCodexBridgeUrl("))
      .toBeLessThan(provider.indexOf("await client.connect(latestBridgeUrl)"));
  });

  it("重连 bootstrap 完成后历史页重新执行 thread/resume", () => {
    expect(provider).toContain('client.request("initialize"');
    expect(provider).toContain('client.request(\n      "thread/resume"');
    expect(historyPage).toContain("const resume = await resumeThread({");
    expect(historyPage).toContain("threadId: id,");
    expect(historyPage).toContain("model: savedPreference?.model");
    expect(historyPage).toContain("permissionProfile: savedPreference?.permissionProfile");
    expect(historyPage).toContain("useAppServerSelector((state) => state.connection.data)");
    expect(historyPage).toContain("connectionData !== 'connected'");
  });

  it("连接变化时保留已加载消息，并在聊天区显示断线提示", () => {
    expect(historyPage).toContain("const switchingSessions = routeIdRef.current !== id");
    expect(historyPage).toContain("if (switchingSessions) {");
    expect(historyPage).toContain("appServerConnectionNotice(connectionData, reconnect)");
    expect(historyPage).toContain("const appServerNotice = connectionNotice");
    expect(historyPage).toContain("readOnly={activeWriterReplayOnly}");
    expect(historyPage).toContain("composerDisabled={connectionData !== 'connected' || sessionSyncing}");
  });

  it("长时间重连失败后停止自动重试，并允许用户手动重连", () => {
    expect(provider).toContain("reconnectDeadlineTimer");
    expect(provider).toContain("window.setTimeout(failReconnect, RECONNECT_FAILURE_AFTER_MS)");
    expect(provider).toContain("client.close()");
    expect(provider).toContain('connection: { source: "web-bridge", data: "failed" }');
    expect(provider).toContain('window.addEventListener("codex-web:reconnect", handleReconnect)');
    expect(historyPage).toContain("actions={connectionNotice.actions}");
    expect(historyPage).toContain("connectionData === 'failed' && !sessionLoadedRef.current");
    expect(historyPage).toContain("const activeTurnVisibility = selectVisibleActiveTurn({");
    expect(historyPage).toContain("appServerSend={canDisplayAppServerThread ? async");
    expect(historyPage).toContain("if (connectionData !== 'connected') throw new Error('Codex app-server 尚未连接')");
    expect(historyPage).toContain("setSessionSyncing(false)");
  });

  it("新会话、分栏、侧聊和非聊天路由都处理失败恢复", () => {
    expect(newChatPage).toContain("appServerConnectionNotice(connectionData, reconnect)");
    expect(splitColumn).toContain("composerDisabled={connectionData !== 'connected' || syncing}");
    expect(sideChatPanel).toContain("composerDisabled={connection !== 'connected'}");
    expect(sideChatPanel).toContain("appServerNotice={connectionNotice}");
    expect(appShell).toContain("connectionNotice && (!isChatRoute || isSplitActive)");
  });

  it("同步门禁覆盖核心发送、计划实施与目标操作", () => {
    expect(chatView).toContain("if (composerDisabled || readOnly) return false");
    expect(chatView).toContain("disabled={composerDisabled || isStreaming}");
    expect(chatView).toContain("pending={goalMutationPending || composerDisabled}");
  });

  it("新任务被接受后进入 Thread 路由，使刷新能够恢复运行态", () => {
    expect(newChatPage).toContain("if (!existingThreadId)");
    expect(newChatPage).toContain("router.push(`/chat/${encodeURIComponent(threadId)}`)");
  });

  it("thread/resume 使用真实 active Turn 水合并清理陈旧运行态", () => {
    expect(provider).toContain("const resumedActiveTurn = activeTurnFromResume(response)");
    expect(provider).toContain("const recoveryTurn = currentThreadTurn ?? readResumableTurn(window.sessionStorage, response.thread.id)");
    expect(provider).toContain("const mergedResumedActiveTurn = mergeResumedActiveTurn(recoveryTurn, resumedActiveTurn)");
    expect(provider).toContain('sourcedActiveTurn(mergedResumedActiveTurn, "app-server.thread/resume")');
    expect(provider).toContain("removeActiveTurnByThread(current.activeTurnsByThreadId, response.thread.id)");
  });

  it("页面进入后台时保存当前标签页的运行 Turn 候选", () => {
    expect(provider).toContain('document.addEventListener("visibilitychange", handleVisibilityChange)');
    expect(provider).toContain('window.addEventListener("pagehide", persistResumableTurns)');
    expect(provider).toContain("writeResumableTurns(");
    expect(provider).toContain("Object.values(store.getState().activeTurnsByThreadId)");
  });
});
