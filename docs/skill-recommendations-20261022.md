# Skill 推荐收起逻辑与地图 MCP 独立包

## 推荐卡片

源码 `b7b26ac9`。构建助手的推荐 Skill 是可选建议；卡片右上角增加关闭按钮，收起后仅保留“查看 Skill 推荐”入口。已勾选项在手动收起和重新展开之间保留，不触发安装或修改草稿。

进入效果验证、切换到试跑、读取历史验证会话或成功接受下一次运行时，如果没有勾选可用推荐项，自动收起推荐卡片。已勾选项继续展示供审阅；请求失败时不会因运行失败自动隐藏。新草稿或新生成的推荐重置收起状态。已安装的推荐从候选列表移除，审阅按钮只使用当前仍可用的勾选项。

174 对应分支的 45 项构建助手测试通过；79 基线适配后的 41 项同类测试通过。两个版本均通过 Next.js 16.3.8 / TypeScript 生产构建。

浏览器使用临时 QA 草稿，在预览及正式入口验证手动关闭、重新展开保留选择、未勾选时运行接受后收起、没有自动安装或修改草稿、没有页面异常。生成结果和运行接受结果仅在该浏览器上下文中提供固定响应，未发起模型推理或写入服务端任务历史。临时草稿删除，测试会话撤销。

## 79 发布

79 原 Web 为 `kai/sapling-preview-web:memory-feedback-20261019-r1`，源码 `a7f26f74`，旧镜像 ID `sha256:5d1a66991c971dd8393a2c117a3e32fd4da28fb2883f0eb07474238b7a9c4b49`。

以该源码为基线，仅适配推荐卡片的两个源文件及相应测试，未用 174 全量前端覆盖 79。适配补丁 [79-source.patch](results/skill-recommendations-20261022/79-source.patch)，SHA256 `b16cba450724580c544b7e35caa0cf87c0b2526a8326c6d11107b255d7ca3cbf`。静态资源从原运行容器完整保留。

新 Web `kai/sapling-preview-web:skill-recommendations-20261022-r1`，ID `sha256:82bd8862a0806d3fb31da2320687dacbbd767af5e7347439bb186054cf3364e8`，59 层，healthy、restart=0。发布目录 `/root/kai-skill-recommendations-20261022`，配置备份 `compose.before.private.json`。仅修改原 compose 的 Web 镜像字段，使用原项目和网络重建 Web，通过配置检查后重载原 HTTPS Nginx。

正式入口 `https://172.20.105.79:3543` 返回 200。该端口绑定实际主机地址，不能使用主机 127.0.0.1:3543 作为健康检查目标。API、Worker、Geo MCP 的镜像、启动时间和重启次数在发布前后相同；Geo MCP 继续为 `kai/geo-mcp:0.3.0`，healthy。地图脚本 SHA256 继续为 `487268283628af8f99eb122b7a809a7468f1e46554c24dd0ba67847d9db5be0e`。

## 174 发布

Web `harbor.shdata.com:5000/agent-studio/amd64/agent-studio-web:skill-recommendations-20261022-r58`，ID `sha256:ac9ed67b8e98d8968b7ff1b15eae66214ad72c2d46a9de49e8d9f732bdce9951`，12 层。正式 Web 与 3501 均 healthy、restart=0。API、Worker 未更新。

发布目录 `/data/agent-studio/releases/skill-recommendations-20261022-r58`，配置备份 `web-r58-runtime.before.private.json`、`web-r58-compose.before.private.yaml`；3501 回滚容器 `axis-web-clarification-compact-20261022-r57-rollback-skill-recommendations-20261022-r58` 保留。

## 地图 MCP 独立交付

独立包 `geo-mcp-0.3.0-20261022.zip`，SHA256 `5dcd8bd1f7c8e1b44f367f0f00e7a1830775b8eb46f38ec3efefa4f8495722b7`。包含服务端源码、可安装 wheel、模板与地图静态资源、原许可证、Docker Compose/Dockerfile、配置示例、令牌生成脚本、KAI 后台接入文件和测试。

源码来自本地 html-preview-expand 开发工作树的 0.3.0 快照。后端模块、模板及 vendor 文件逐项 SHA256 与 79 正在运行的 `kai/geo-mcp:0.3.0` 一致。31 项服务测试通过；从交付 wheel 解压实际服务后验证健康接口 200、未授权 MCP 401、授权 `tools/list` 200 且包含 10 个工具，验证新令牌哈希匹配、原值文件权限 0600、拒绝覆盖既有凭据。

包内没有线上令牌、私有配置、规划数据库或历史任务。默认 OpenFreeMap、Photon、OSRM 不要求商业地图 API Key，MCP 自身使用独立 Bearer 令牌。初始化脚本在使用者机器上生成新令牌。自驾/骑行与公交等可选能力仍需配置对应 Valhalla、OTP 服务及数据；交付不包含路由引擎镜像或 GTFS 数据集。

验证记录见 [结果目录](results/skill-recommendations-20261022/)。
