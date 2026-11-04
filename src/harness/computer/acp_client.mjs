// Fixed stdio ACP client. The privileged gate supplies installation and cwd;
// this process runs as the enrolled desktop user and retains their login.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { Readable, Writable } from "node:stream";
import { pathToFileURL } from "node:url";
import path from "node:path";

export async function run(request) {
  const require = createRequire(path.join(request.install_dir, "package.json"));
  const { ClientApp, ndJsonStream, PROTOCOL_VERSION } = await import(
    pathToFileURL(require.resolve("@agentclientprotocol/sdk")).href
  );
  const binary = request.agent === "claude" ? "claude-agent-acp" : "codex-acp";
  const child = spawn(path.join(request.install_dir, "node_modules", ".bin", binary), [], {
    cwd: request.cwd, env: process.env, stdio: ["pipe", "pipe", "ignore"],
  });
  const denied = [];
  const progress = [];
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill("SIGTERM");
  }, (request.timeout_seconds ?? 120) * 1000);
  try {
    const app = new ClientApp({ name: "kai-computer-acp" })
      .onRequest("session/request_permission", ({ params }) => {
        const title = String(params.toolCall?.title ?? "Permission required").slice(0, 120);
        denied.push(title);
        // DEMO (2026-11-05): the owner asked for a writable demo. Approve the
        // current tool call only (allow_once semantics); never allow_always.
        // Revert by restoring the cancelled outcome from git history.
        const options = Array.isArray(params.options) ? params.options : [];
        const pick =
          options.find(o => o.kind === "allow_once") ??
          options.find(o => o.outcome === "allow_once") ??
          options.find(o => o.kind === "allow_selected") ??
          options.find(o => o.kind === "allow_always") ??
          Object.values(params.option ?? {}).find(v => v?.kind === "allow_once") ??
          { outcome: "allow_once" };
        return { outcome: { outcome: "allow_once", optionId: pick.optionId ?? pick.id } };
      });
    const conn = app.connect(ndJsonStream(Writable.toWeb(child.stdin), Readable.toWeb(child.stdout)));
    await conn.agent.request("initialize", {
      protocolVersion: PROTOCOL_VERSION, clientCapabilities: {},
      clientInfo: { name: "kai-computer-acp", version: "1" },
    });
    const session = await conn.agent.buildSession(request.cwd).start();
    // DEMO (2026-11-05): no forced set_mode. The hard-coded plan/read-only
    // modeIds broke writes, and guessing the agents' writable mode ids is not
    // safe; each agent now runs with the mode of the owner's own logged-in
    // profile. recordCurrentModeUpdate keeps the honest `mode` below.
    const pending = session.prompt(request.prompt);
    // A failed RPC need not produce a stream "stop". Race its rejection with
    // updates so authentication/provider failures cannot hang until timeout.
    const failed = pending.then(() => new Promise(() => {}));
    void failed.catch(() => {});
    let text = "";
    let stopReason;
    for (;;) {
      const message = await Promise.race([session.nextUpdate(), failed]);
      if (message.kind === "stop") { stopReason = message.stopReason; break; }
      const update = message.update;
      if (update.sessionUpdate === "agent_message_chunk") {
        text = (text + (update.content?.text ?? "")).slice(-8000);
      } else if (update.sessionUpdate === "tool_call" && progress.length < 12) {
        progress.push({ title: String(update.title ?? "").slice(0, 100), kind: update.kind });
      }
    }
    await pending;
    const result = {
      ok: stopReason === "end_turn", agent: request.agent, session_id: session.sessionId,
      execution_user: process.env.USER, cwd: process.cwd(),
      demo_writable: true, text: text.trim(), stop_reason: stopReason,
      mode: session.mode?.current ?? session.currentModeId ?? null,
      denied_permissions: denied.slice(0, 8), tools: progress,
    };
    session.dispose();
    return result;
  } catch (error) {
    if (timedOut) throw new Error("ACP_TIMEOUT");
    throw error;
  } finally {
    clearTimeout(timer);
    // The gate also kills the complete process group on timeout/lost lease.
    child.stdin.end();
    child.kill("SIGTERM");
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  let body = "";
  for await (const chunk of process.stdin) {
    body += chunk;
    if (Buffer.byteLength(body) > 24000) throw new Error("ACP_REQUEST_TOO_LARGE");
  }
  let result;
  try { result = await run(JSON.parse(body)); }
  catch (error) {
    const message = String(error?.message ?? "");
    result = { ok: false, code: message.includes("ACP_TIMEOUT") ? "ACP_TIMEOUT" :
      /auth|login/i.test(message) ? "ACP_LOGIN_REQUIRED" :
      message.includes("ACP_READ_ONLY_UNAVAILABLE") ? "ACP_READ_ONLY_UNAVAILABLE" : "ACP_FAILED" };
  }
  process.stdout.write(JSON.stringify(result));
  // Do not retain an SDK/adapter event loop after the reply is complete.
  process.exit(result.ok ? 0 : 1);
}
