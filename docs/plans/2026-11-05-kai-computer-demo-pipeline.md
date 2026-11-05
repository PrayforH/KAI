# KAI 电脑演示一条龙（云电脑 + 本机电脑）

**主线**：今晚起 ZCode 核心任务只推本线；散点 UI/flash 路由/vault/真 IM 暂缓，除非用户点名。
**工作树**：本机 ACP/gate 改动优先 `kai-computer-repair` / `fix/local-computer-link-20261104`；云/remote 目标与 viewer 行为参考 `kai-remote-computer` / `feat/remote-computer-20261103`（勿从 Documents 脏区迁 diff）。
**模型**：`account:zai-start-plan` / `GLM-5.3-Flash`（夜间）；不可用改 DeepSeek-flash；**禁止 MiniMax**。
**环境**：79=`https://172.20.105.79:3543/assistant`，compose `/data/kai-79-20261013`；本机 enrolled gate（32.45 线 + Mac `/opt/kai-computer` 视登记为准）。

## 演示故事（一条线）

1. 打开助手 → 编码协同仍有历史（binding 已修）
2. 打开电脑 → 选 **本机电脑** → 桌面可见；编码协同用 `computer_acp` **可写演示**（不再只读）
3. 空闲时切换执行机到 **云电脑** → 桌面可见；短命令/`pwd`/`hostname` 回主会话
4. 再切回本机；人工接管时助理输入被拒，交还后恢复
5. 全程：租约/epoch/回执正确；不碰 vault、不碰 174、不碰 flash-only 坏发布

## 交付物形态（用户要求）

最终「演示脚本」不是纯操作步骤，必须含：

1. **前置技术调研**：Codex/Claude ACP 可写 mode 与 permission 语义；本机 enrolled gate vs 云 VM vs remote-computer 边界；79 computer-control/viewer 健康与 BlockingIO 类故障；现网镜像/配置快照。
2. **架构说明**：ComputerRegistry / opaque target / 执行绑定 vs 仅查看；租约·epoch·接管；ACP 走本机桌面用户 HOME 的路径；云电脑（vm-computer）与 remote-computer 差异；主助理→编码协同→电脑工具→回执链路图（文字+可选 mermaid）。
3. **演示剧本**：按故事点可跟点的步骤 + 预期画面/回执 + 失败时判读。
4. **验收与回滚**：每环判据、镜像 tag、如何退回只读 ACP。

落档：`docs/results/2026-11-05-kai-computer-demo.md`（调研+架构+剧本合一）；计划本文持续更新进度。

## 阶段（串行，勿叠派）

| 环 | 标记 | 内容 | 完成标准 |
|---|---|---|---|
| D0 | `kai-computer-acp-write-demo` | 去掉 ACP 强制 plan/read-only + permission cancelled；文案改 DEMO 可写；测+滚 computer-control + 刷本机 gate；**同步记下调研摘记**（modeId、permission outcome） | 工具描述无「只读」；permission 可允许；79/本机一致；可回滚；调研摘记进结果档 |
| D0.5 | `kai-computer-demo-research-arch` | **前置调研+架构说明成稿**（可与 D0 收尾合并）：对照 Codex 最新 ACP、现有 remote-computer 计划/11.17–11.20 文档、79 实况；画主→Bot→本机/云切换架构 | 结果档含调研结论、架构图、已知缺口；无代码也可先交档 |
| D1 | `kai-computer-control-health` | 修 79 `computer-control` unhealthy（BlockingIOError/资源）；identity 冒烟 | compose healthy；真实 control identity 通 |
| D2 | `kai-computer-local-smoke` | 本机目标：列表→查看→执行租约→ACP 写一小文件（合成路径）→回执 | 真机结果回聊天；不写用户业务仓 |
| D3 | `kai-computer-cloud-smoke` | 云电脑目标：切换查看+执行；短命令；与本机互切拒绝活跃 Run | opaque target；切机 epoch+；桌面可见 |
| D4 | `kai-computer-demo-script` | 把调研+架构+操作剧本收成可演示脚本；补截图位/预期文案；必要时只滚 web/api | 用户可只读该文档跟点一遍完成演示 |

## 派活规则

- 只用 `session_8f899c746a52bd27b9a3c8037476cc47` + `zcode-dispatch-oneshot`；idle≥90s 再派下一环
- 每环写短结果到 `docs/results/`；失败如实记，不假报成功
- computer-control / api / worker / web 按需滚；**不要**再上 ff383602 flash-only
- 例程 `zcode-agent-studio` 恢复后只盯本线：空闲则派下一未完成环；有 running 勿叠

## 当前进度

- 历史绑定（编码协同）：已 SQL 修好，待用户刷新确认
- D0：✅ `03db5621` + control 镜像 `20261104-acp-demo-03db5621` healthy；结果 `docs/results/2026-11-05-acp-demo-writable.md`（约 00:49 完成）
- D0.5：✅ 调研+架构+剧本+验收合稿 `docs/results/2026-11-05-kai-computer-demo.md`
  （79 实况快照、digest 三方不一致证据链、mermaid 架构、S0–S5 剧本、退回只读步骤）
- D1：✅ control `20261104-acp-demo-03db5621` healthy（BlockingIO 卡死为复发型，重启恢复）；
  identity 冒烟仍被 gate digest 阻塞（依赖 D2 清单 1–3 步，见合稿 §1.4/§4.3）
- D2：✅ 产品路径冒烟通过（`docs/results/2026-11-05-kai-computer-local-smoke.md`）：
  digest 双侧对齐 `72b329d6…`、identity PASS、acp_client selected+optionId（`9ed42ee1`）、
  真实 Run `run_d741a8b5021049c4be78e8f06132c1ee` 经 computer_acp 写读
  `/home/xiaokai/acp-test/D2_PRODUCT_ACP.txt`=`D2_ACP_OK` 一致；`demo_writable:true`。
- D3：✅ 云电脑切换冒烟通过（`docs/results/2026-11-05-kai-computer-cloud-smoke.md`）：
  切 cloud（revision 385）→ 真实 Run `run_f6323257…` 云 VM `hostname=kai-aaaa…`（Chassis: vm）→
  切回 local-32-45（revision 391）；lease start+execute 成对 released
- D4：✅ 最终演示脚本 `docs/results/2026-11-05-kai-computer-demo.md`（S0–S5 可只读跟点，
  嵌 D2/D3 真实锚点与截图位；调研/架构/回滚同档；含云上 Bash=沙箱策略 vs computer_acp 话术区分）
