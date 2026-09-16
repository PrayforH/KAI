# 沙箱运行下的图片投递（174/173，2026-09-16）

## 问题

多轮对话里把模型切到多模态路由（`deepseek-v4-flash` → 官方名 `deepseek-flash`）后，上传图片提问，
模型仍回答"我没有视觉通道"，随后在沙箱里 `pip3 install rapidocr-onnxruntime opencv-python-headless` 自行装 OCR。

对照运行记录（174，`session_8242cfae…`，2026-09-16 09:41）：

| 运行 | 工具集 | 结果 |
| --- | --- | --- |
| `run_9b52ca7a…`（09-08，"只回答左右图形颜色形状"） | 原生 `Bash, Glob, Grep, Read, Write` | succeeded，事件里有 `Read inputs/original/…-qa-shapes.png` |
| `run_4c6e60c0…`（09-16，"能识别图吗" + 1 张图） | `mcp__harness-sandbox__read/bash/edit/glob/grep/write` | cancelled；模型推理原文 "I don't have a vision channel. But I can use OCR…" 后 `pip install` |

根因不在路由（`model.route.selected`、`required_model_capabilities=["vision"]` 均正确），而在**投递**：

- 运行时把上传件只落成工作区文件，并在 prompt 里提示 "Read an original directly … such as for an image"，
  依赖 SDK **原生 Read** 把图片作为 base64 图像块返回（`claude_sdk.py` 注释与 `max_buffer_size` 即为它而设）。
- 一旦本次运行由沙箱承载（`sandbox_proxy_enabled = executor is not None and provider != "local"`），
  原生工具被替换成 `mcp__harness-sandbox__*`，而沙箱 `Read` 的声明是
  `"Read a UTF-8 text file from the isolated workspace."` —— 图像通道消失，模型只能看到路径。
- 174 是 CubeSandbox（`HARNESS_SANDBOX_PROVIDER=cubesandbox`），173 是 worker 本地执行
  （`provider=local`，保留原生工具），所以只有 174 暴露了该缺陷。

## 改动（`cf97d79`）

参考 deerflow 的思路（专用 `view_image` 工具 + 中间件注入、只在模型支持视觉时启用），
按我们现有架构收敛为四点：

1. **沙箱 `Read` 支持图像，服务端掌管策略**（`src/harness/runtime/sandbox_tools.py`）
   - 新增 `_MAX_IMAGE_BYTES = 10 MiB`（受 CLI 32 MiB JSON 缓冲与 base64/转义余量约束）与 PNG/JPEG/WebP/GIF 白名单。
   - 沙箱侧脚本按 magic bytes 识别格式、超限时返回明确错误（不做截断，避免模型拿到"坏图"）。
   - 代理侧再用服务端旗标（`_image_mode`、`_image_max_bytes`）强制注入并复核回包信封（MIME 白名单、base64 长度上限），
     模型无法关闭、放大或改小该策略；未通过复核则退化为文本结果。
   - 保持工具名不变（仍是 `mcp__harness-sandbox__read`），不动策略规则与授权模型。
2. **视觉门控**（`src/harness/runtime/claude_sdk.py`）
   - 仅当所选路由 `capabilities` 含 `vision` 时才开启图像读取（`image_aware`），工具描述同步说明"读图返回图像本身"。
3. **契约引导**（同一文件）
   - 有视觉：读 PNG/JPEG/WebP/GIF 会返回图像本身，直接从图回答，不得安装 OCR 或图像库。
   - 无视觉：明确告知本运行不具备图像能力，不要尝试 OCR 或安装依赖，改为引导用户提供文本/表格/PDF。
4. **事件脱敏**（`src/harness/runtime/message_mapper.py`）
   - `tool.result` 中的图像块统一降级为 `{type: image, media_type, base64_chars, omitted}`，
     运行事件（持久化 + AG-UI 回放）不再携带 base64。上传件的工具结果另有既存输入脱敏层兜底。

## 测试

- 新增 `tests/unit/runtime/test_sandbox_image_read.py`（远程脚本图像信封/文本回退/超限报错/服务端策略强制/无视觉不返回图像/信封拒收）。
- `tests/unit/runtime/test_message_mapper.py` 增补两种图像块形状（MCP `data+mimeType`、Anthropic `source`）的脱敏断言。
- `tests/unit/runtime/test_claude_input_files.py` 覆盖有/无视觉两条契约文案。
- develop 基线全量单测 **1130 passed**；`ruff check` 仅剩 4 条既有 E501（`test_approval_flow`、`test_model_configuration`，与本次无关）。

## 部署

| 环境 | 执行方式 | 镜像 | 发布方式 |
| --- | --- | --- | --- |
| 174 | CubeSandbox | `kai/axis-api:image-input-20260916`（基于 `kai/axis-api:dsh-process-20260916` 的应用代码增量镜像；web 复用 `kai/axis-web:attachment-layout-20260916`） | `/data/image-input-20260916/`（release/rollback compose + `switch.sh`：活跃运行守卫 → 重建 api/worker/web → 健康检查 → 失败自动回滚） |
| 173 | worker 本地 | `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-api:develop-20260916-cf97d79`（web 同 tag，digest 与上一版本一致即未重建） | 备份 `.env.production.bak-20260916-*`、记录 `.prestate-*`、切 tag → migrate（空跑，schema 仍 0032）→ 重建 api/worker/web/quality-sync |

镜像自检：`kai/axis-api:image-input-20260916` 内 `pkgutil.walk_packages` 全量导入 **277 模块 0 失败**。

## 验证（真实带图运行，非源码断言）

两个环境各跑一次真实运行：上传 64×64 纯红 PNG → `helper-agent@1.0.0`（claude-agent-sdk，Read/Glob/Grep）
→ `model_route_override=deepseek-v4-flash`（模型 `deepseek-flash`，vision）+ `required_model_capabilities=["vision"]`。

| 环境 | 运行 | 事件证据 | 答复 |
| --- | --- | --- | --- |
| 174 | `run_80c572d3f9524b95af63f922fdd732e8` | `input.staged` → `model.route.selected(deepseek-v4-flash, caps=[streaming, tool_use, vision])` → `tool.request Read inputs/original/…-verify-red.png` → `tool.result [Input file content omitted from durable events]` | **Red** |
| 173 | `run_5131fac4bd994517bb0e297f4821150a` | 同上（`Read` 走原生路径） | "I'll inspect the image directly. **Red** … is solid red." |

两次运行都只有 1 次 `Read`、0 次 `Bash`、无 `pip`/`rapidocr` 痕迹；事件体积 ~512/262 字节，证明图像字节未入库。

## 回滚

- 174：`sh /data/image-input-20260916/switch.sh` 已含失败自动回滚；手工回滚执行
  `docker compose -f /data/image-input-20260916/compose.api.rollback.private.json up -d --no-deps --force-recreate --wait --scale worker=3 api worker`
  （回到 `kai/axis-api:dsh-process-20260916`）与对应 web 回滚配置。
- 173：把 `.env.production` 的 `HARNESS_HARBOR_IMAGE_TAG` 改回 `develop-20260916-c59e8fb`（或 `.env.production.bak-20260916-*` 里的值）后重建 api/worker/web。

## 遗留

- 174 的 quality-sync 仍跟随其自身 tag（`general-agent-contract-20260915-1915`），未随本次升级。
- 173 的 `codex-deepseek-v4-flash`（openai_compatible，Codex runtime）仍为 `modelType=chat`；模型本身可读图，
  如需 Codex 侧收图需另行开启 vision。
- deerflow 的若干稳健性细节可后续补齐：允许路径白名单（仅 uploads/outputs/workspace）、
  扩展名与 magic bytes 一致性校验、错误信息脱敏（当前沿用既有沙箱错误处理）。
