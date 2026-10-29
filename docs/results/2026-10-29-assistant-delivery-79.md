# 79 助理结果卡与文件交付验收（2026-10-29）

本次完成结果卡简化、飞书与微信原生文件交付、认证预览/下载、刷新保留及三端同会话续聊。入口：<https://172.20.105.79:3543/assistant>。此结论不代表原完整云电脑方案的 BrowserTools、frame/boot 绑定或 P2 平台项已经完成。

## 实现

- 助理实际入口 `/assistant` 默认收起处理过程，只展示简短状态、回答与文件卡；失败说明保留，过程可手动展开。文件卡直接预览、下载，复用现有 HTML/PDF/图片/Office 预览器，HTML 保持独立 sandbox。
- `assistant_channel_files`（迁移 0054）按通道、源消息、Artifact 建立稳定交付身份。上传引用和消息回执加密保存，上传与发送分别提交；每一步重验本人账号和当前绑定，解绑后不发送。文件状态可在通道设置中查询。
- 飞书复用官方 SDK 的稳定 UUID，在去重窗口内重试；缺上传权限会阻塞，管理员开通后通过本人重试接口恢复，不重跑模型。
- 微信按腾讯公开协议执行 AES-128-ECB/PKCS7 CDN 上传及 FILE 消息发送。发送结果不确定时保留 `uncertain`，明确重试动作也不会盲目重发这类消息。
- 修复跨轮旧文件发布失败：差量采集只镜像变化文件，原先的发布器误以为未变化旧文件不存在。现在通过受限文件平面按需读取指定文件，保留工作区路径、大小、下载签名与哈希校验，无需改名、改写或重新生成原件。

## 发布

| 服务 | 镜像 | Image ID |
| --- | --- | --- |
| api / worker / computer-control | `kai/assistant-api:20261029-dot19-results` | `86dc5a9ecf45280c2e89a43414185596505db620e7cc1b3b88389feb01f5e32c` |
| feishu | `kai/feishu-receiver:20261029-dot19-results` | `31a548e8ce4147da35f0d5092233897c00157644dd70b18cbfdd8d1577fa6835` |
| weixin | `kai/weixin-receiver:20261029-dot19-results` | `8e067d1d034adb3dce1dfe14d27ebf9f7f65e9dfa32b3b1e924159415fdfa99d` |
| web | `kai/assistant-web:20261029-results3` | `bd19d420786c70b79cf16b8656c76e7b0b0deebb1f6f51bff91ab71c279c1285` |

Web 使用 Node 22 下的完整 Linux standalone 构建及匹配依赖，未把新版 `.next` 覆盖到旧依赖。Viewer 保持上一轮已验证的 v12-clipboard。仅逐服务切换，未重建本人 VM、根盘或浏览器配置，账号绑定与原聊天均保留。

部署目录 `/data/kai-result-delivery-20261029/`；私有 compose 备份 `/data/kai-79-20261013/compose.private.before-results-20261029.json`。回滚按服务恢复旧镜像；保留 0054 表及新交付记录，先确认没有在途发送再停接收器，不做数据库降级或删除新数据。

## 真实验收

1. 本人飞书客户端发送 DLV79-1029-B，Run `run_6e57a4c4f1384d23837f51cb1775429d` 在 VM 创建并发布 TXT、HTML。
2. 飞书上传最初真实返回 99991672，缺 `im:resource:upload`。用户确认管理员已开通生效；调用本人重试接口返回 `queued: 4`，四份验收文件均变为 `sent`，加密记录中保存了实际消息回执。未重新执行生成任务。
3. 用户本人微信发送续聊，Run `run_981c1d60f46b4c0cb4961451d605a3dd` 正确复述 `LANTERN-79-B` 并返回原生文件。用户随后明确确认：飞书与微信两端 TXT、HTML 都能下载并打开。发送者侧直接拼接 CDN URL 的探针返回 400，不作为接收端下载验证；接收端确认与网页认证下载分别记录。
4. 浏览器内真实点击 HTML 卡片并显示正文；实际点击下载，在本机 Downloads 得到 `交付报告-DLV79-1029-A.html`，903 B，SHA256 `96ead1b6429d26646917365617850074d68829f35241f2edb508c92cb87e885a`，与 Artifact 元数据一致。
5. 从网页继续同一会话，Run `run_fe2dfc59851045459ccd62454fb64054` 成功回忆微信口令，并直接按原名发布 `delivery-DLV79-1029-B.txt`、`report-DLV79-1029-B.html`。文件哈希分别为 `1d910b1b90a404f612e3277fd9ceb88946df1378f3cf7a7a0d423d278f572d44`、`36cbf52f4b7c556947f5bd24dda9a2c39e166a1df936d1ba7964b9045f90ccc9`，与前轮一致。
6. 三个 Run 均 succeeded、同一个 session。真实浏览器刷新后，三端历史、网页续聊回答及两个新文件卡仍在，桌面重新显示“已连接 / 观察中”。七份通道文件的认证下载与预览均 HTTP 200，字节哈希匹配；匿名访问被拒绝。

结构化证据：[2026-10-29-assistant-delivery-79.json](2026-10-29-assistant-delivery-79.json)。不保存任何 token、上传引用、解密密钥或私有 compose 内容。

## 检查

- 受影响 Python 测试：**191 passed, 1 skipped**；跳过的是本地缺少固定版本微信语音 codec 的既有用例，不计入本次文件交付通过证据。
- 前端结果卡、过程披露及既有 activity UI：**33 passed**。
- TypeScript、Ruff、`git diff --check` 通过；Linux Next.js 生产构建成功；发布后 API/worker/control/web 健康，两个接收器在线。
- 故障测试覆盖：上传中断恢复、稳定消息身份、账号解绑、跨 Run 文件拒绝、权限阻塞与本人重试、跨用户重试拒绝、微信未知回执不重发、加密内容与 CDN 地址边界、跨轮未变化文件按需发布。

原方案中尚未实施的 BrowserTools（CDP/DOM）、frame 与 VM boot 绑定、模板派生、每用户 Viewer、配额、OpenSandbox SDK 薄适配和快照仍单独保留为后续范围。
