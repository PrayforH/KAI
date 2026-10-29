# 侧栏、D1 与双沙箱配置

模型选择器与语音输入共用输入框右侧控件组，保持输入框尺寸。侧栏统一图标从 18px 缩为 16px；项目、任务分组以及单个项目支持展开收起，分组选择按用户保存在浏览器。

D1 使用独立的 `HARNESS_STUDIO_BUILDER_INTENT_*` 配置，自动模式先判定 edit/run/rerun/ask/reply。超时或协议异常回退原有自动判断，原回答模型绑定不变；修改仍经过预览、权限、版本及配置一致性校验。此接入只合并 D1 任务对应的补丁和测试，保留当前 develop 的响应格式修复逻辑。

部署可以通过 `HARNESS_SANDBOX_PROVIDER` 与 `HARNESS_SANDBOX_EXTRA_PROVIDERS` 同时启用两类资源。通用助手及未固定沙箱的会话使用部署默认；新建智能体优先 OpenSandbox，其他智能体可在环境策略中选择执行配置。已发布快照和已有会话保留原有固定配置，不迁移或删除 warm 沙箱。

| 部署 | 默认 OpenSandbox | CubeSandbox |
| --- | --- | --- |
| 109 网段 / 174 | `http://172.20.109.113:14090` | `http://172.20.109.111:13000`，proxy `:80` |
| 105 网段 / 79 | `http://172.20.105.79:24090` | 同上 |

`HARNESS_SANDBOX_NETWORK_ACCESS` 声明每个后端实际可达的网络：默认 OpenSandbox 为 `["none","internal","external"]`，CubeSandbox 为 `["none"]`。该配置是平台能力上限，不会授予智能体工具权限，也不会修改沙箱服务器网络。用户能力目录、编译、试跑和发布策略共用部署能力配置，只展示已启用后端，网络能力与部署配置取交集；保存的资源目录不被覆盖，切换部署配置可恢复原有资源。

113、79 现有 OpenSandbox 服务使用不要求 API Key 的模式，因此连接端显式配置 `HARNESS_OPENSANDBOX_AUTH_MODE=none`；代码默认仍为 `api_key`。使用服务器已存在的 `harbor.shdata.com:5000/deerflow/sandbox:1.11.0` 镜像。CubeSandbox 保留原有模板别名、密钥和 warm 策略。
