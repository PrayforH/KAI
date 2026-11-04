# D2 本机电脑产品路径冒烟：computer_acp 真实写入（2026-11-05）

**结果：✅ 通过（产品路径，含 lease）。** 一次真实 Run 在编码协同 Bot 里调
`computer_acp`（claude），在本机 32.45 的 `/home/xiaokai/acp-test/D2_PRODUCT_ACP.txt`
写入 `D2_ACP_OK` 并读回一致。

## 走的入口（写清，便于复现）

1. **session token**：79 api 容器内按 `AuthService._session_response` 同构 mint
   （HS256、iss/aud 同 Settings，sub=`user_1c16a8994ff548c298c356fd8385eb76`、
   roles=owner、sid=`d2-smoke-family`）；因 `current_user` 校验 token family，先向
   `refresh_tokens` 插入一行同 `family_id` 的家族记录（1 小时有效）。
   ⚠️ 该行是测试会话家族，演示后可删：
   ```sql
   delete from refresh_tokens where family_id='d2-smoke-family';
   ```
2. **选 target**：`POST /v1/studio/assistant/computer/targets/select`
   `{"target_id":"local-32-45"}` → `active_target=local-32-45`（revision 379）。
   第一次尝试 409：桌面租约 `computer-run-d0dd…` state=agent epoch=213 到期前 10s 仍
   valid；等 15s 过期后选择成功（租约到期自动释放，符合 idle-only 设计）。
3. **真实 Run**：`POST /v1/agui?agent_name=archive-assistant-agent&agent_version=0.1.1`
   thread=`assistant_specialist_7bb61192fe1541bf90a815f40c670932`（编码协同），
   run=`run_d741a8b5021049c4be78e8f06132c1ee`，prompt 要求用 computer_acp(claude)
   在 `/home/xiaokai/acp-test` 写 `D2_PRODUCT_ACP.txt`（内容 `D2_ACP_OK`）并读回。

## 证据

- **Run**：`run_d741a8b5021049c4be78e8f06132c1ee` status=`succeeded`
  （session_21e7ebed55b8407da5dd8874b7ca206e）。
- **工具调用**：`tool.request` seq=61 `mcp__harness-builder__computer_acp`
  args=`{agent:claude, cwd:/home/xiaokai/workspace/KAI, prompt:"…写 D2_PRODUCT_ACP.txt…"}`。
- **工具回执**（seq=124，`is_error:false`）：
  ```json
  {"ok": true, "agent": "claude", "execution_user": "xiaokai",
   "cwd": "/home/xiaokai/workspace/KAI", "demo_writable": true,
   "mode": null, "stop_reason": "end_turn",
   "text": "…printf 'D2_ACP_OK\\n' > /home/xiaokai/acp-test/D2_PRODUCT_ACP.txt …
            cat … 原始输出：D2_ACP_OK … 文件内容已确认为 D2_ACP_OK。"}
  ```
  ACP agent 自己用 cat 读回 **`D2_ACP_OK`**——写入与读回一致。
- **租约链**：同一 run 名下的两条 execution lease（`start` + `execute`，
  `computer_lease_9daaf407…` / `d30ffb03…`，状态 released，21:36:30→21:38:30）+
  machine 侧 `state.json` active 语义由 gate 在 activate 时写入——产品路径全程带 lease。
- **桌面租约**：该 run 前有一条 agent 租约（epoch 213）到期自动释放后我们才拿到
  target（切机 idle-only 生效的一次实证）；事后桌面状态回到 `observe` epoch 215。

## 字段记录（marker 要求）

- `session_id`（ACP transport 层）：**未出现在回执**——D0 时已设计为不进对话面
  （`test_internal_acp_session_handle_is_not_conversation_content`），tool.result 无此字段，如实记。
- `denied_permissions`：3 条（mkdir / printf 写入 / cat）——**这是 permission 提示被
  allow_once 放行前 client 记录的"曾经请求过"清单，不是拒绝**；对照 `tools[]` 三个
  Terminal execute 调用全部完成、最终 cat 返回内容，可证未实际拦截。
- `mode`：`null`（D0 去掉强制 set_mode 后 agent 未上报 mode id，符合预期、如实记录）。
- `demo_writable: true`；`execution_user: xiaokai`（桌面用户身份，非 root）。

## 过程中的坑（写清避免复踩）

1. **token family**：只 mint access token 会被 `current_user` 拒（`登录会话已结束`），
   必须先建 refresh_tokens 家族行。
2. **桌面租约残留**：上一次 run 的 agent 租约（epoch 213）在 `expires_at` 前 10s 仍会
   409，等过期即可，不要手工清 DB。
3. **curl 不在 api 容器里**：从宿主机 curl :8800 即可（targets 路由无内网限制）。
4. `v1/studio/assistant/computer`（无 targets 后缀）是 404——状态接口路径不是这个，
   面板用的是 `/computer/status`（web BFF），不在本环验证面内。

## 结论

D2 ✅：产品路径（真实 Run + lease + gate digest 对齐 + DEMO 可写 client）在本机
32.45 上完成写入并读回一致。合稿 §1.4/§4.3/§六.3 与计划表同步更新。
