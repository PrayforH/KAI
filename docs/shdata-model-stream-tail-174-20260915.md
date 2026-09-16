# shdata-model 流式尾部截断修复（174，2026-09-15）

## 结论

根因位于 New API v1.0.0-rc.21 的 OpenAI → Anthropic 流式转换。上游将最后一段正文与 `finish_reason=stop` 放在同一个数据包，网关在等待后续 usage 包时提前返回，导致该包正文丢失，最终仍返回 `end_turn`。

174 的 `shdata-model` 原先随机选择同优先级的两个通道：

| 通道 | 上游 | 转换方式 | 同一段 378 字文本的结果 |
| --- | --- | --- | --- |
| 13 | 私有 vLLM，原生 Anthropic | 原生 Messages | 2/2 次完整，378 字 |
| 14 | 同一私有 vLLM，OpenAI | OpenAI 转 Anthropic | 2/2 次截断，371 字，缺 `739182。` |

上游 OpenAI 原始流的最后一个 choice 同时包含 `delta.content="739182。"` 与 `finish_reason="stop"`，证明尾字已经由模型生成。原生 Anthropic 流也包含完整尾字。

## 用户运行的证据

截图对应 `run_fb1e3c65ff534e1da671f64998147adc`，2026-09-15 22:21（上海），提示为“你能帮我做什么？”。

- 聊天事件与 SDK 会话存档均为 917 字，末尾都停在“给你看看当前数据里都有”。
- SDK 报告成功、`end_turn`，输出 621 tokens。
- New API 日志 125285 指向通道 14。
- 另外两次长文本运行（输出 1,537、3,095 tokens）同样落在通道 14，尾部截断。不存在统一输出 token 上限的特征。

## 实际修复

通过 Studio 模型配置 API，将 `shdata-model` 的 New API 请求固定到通道 13，使用网关已有的管理员令牌指定通道机制。模型目录 revision 49 → 50。

模型名称仍为 `shdata-model`，请求仍经过原有网关。修复限于 Studio 此模型的通道选择，无需重启服务；后续运行会使用新配置。已截断的历史消息不做推测性补字。

本次采用原生通道规避转换缺陷，没有声称修复或升级全局 New API 转换器。

## 验证

- 配置前指定通道对照：[channel-comparison](results/shdata-stream-tail-channels-20260915.json)。
- 配置后普通请求路径连续完整，378/378 字，含末尾标记与 `message_stop`。
- 真实舆情智能体、显式 `shdata-model`，同一会话连续三轮：
  - 54/54 字，`run_cd90b140cc2a49ffa1d825e7c83bfb95`。
  - 738/738 字，`run_1e1851b00abc48899b7df2c7a3049429`。
  - 5,999/5,999 字，`run_891d821c9d6946e4a7b7692784af8e89`。
- 三轮均 succeeded，逐字匹配预期，结尾标记完整。记录见 [短/中回复](results/shdata-stream-tail-e2e-20260915.json)、[长回复](results/shdata-stream-tail-long-20260915.json)。
- 新增 [smoke_model_stream_tail.py](../scripts/smoke_model_stream_tail.py) 可重测流式尾部完整性；Ruff、Pyright 通过。仅输出状态与字数，不输出凭据或原始思考事件。

## 上游代码依据

- [转换器提前返回的位置](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.21/service/relayconvert/internal/oai_chat/to_claude_messages_resp.go#L293-L302)：遇到结束标记且 usage 尚不可用时，处理正文之前就返回。
- [指定通道机制](https://github.com/QuantumNous/new-api/blob/v1.0.0-rc.21/middleware/auth.go#L447-L478)：管理员所有的既有模型令牌可以选择指定通道。

## 运维记录

远端记录目录：`/data/shdata-stream-tail-20260915`。其中 `rollback.private.json` 仅保存原模型元数据与加密凭据，权限为 0600，不进入代码仓库。若需回退，使用原加密凭据解密后的令牌通过模型配置 API 恢复，并读取当时最新目录 revision；不覆盖其他模型的并发变更。
