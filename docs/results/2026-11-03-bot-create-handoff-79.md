# 先创建Bot，再由Bot完成配置与工作（79）

2026-11-03；用户指出工时请求只得到澄清，没有创建Bot，明确“创建了之后，由工时填写去干生效的事情”“代码优雅”。独立kai-weather-fix／feat/kai-assistant-routing-20261103；功能72ce1d5c，共享投递重构fac266e9。

## 原因与实现

原请求run_99d6bd48a799413dab3da010f690b096只执行route=clarify，本人存储确无工时Bot。创建联系人被后续缺登录和业务规则一并阻塞。

主助理路由新增assistant_create_specialist：明确新建时选direct/management，先创建或复用联系人，再将原始需求交给它。登录态、规则和生效配置由子Bot自己核对、提问和执行；主助理简短确认后结束。子Bot收到“帮我创建Bot”的原话，知道自己已创建，直接处理业务，不递归创建自己。

复用create_specialist的本人已发布能力绑定、slug去重与持久随机外观；复用send_mention_message/AG-UI/Run/来源行与回传。已有Bot路由和新Bot初始消息统一经过一个_deliver方法，处理原话、引用、父请求状态、原权限、稳定请求键与实际投递事件。创建意图使用现有EventService.append_once；相同父请求不能换名另建，重试、并发和恢复沿用同一Bot和投递。

创建与送达分别留证。投递失败时保留Bot、明确未送达，重试继续原投递；新附件不能完整转交时保留Bot但不丢文件执行。空目录也能创建首个Bot。不新建Objective、周期、权限、凭据或第二套执行器。普通任务、项目和子助理自身不装配主路由工具；Codex执行入口既有平台工具限制保留。

## 检查与发布

相关后端248通过，1项既有Starlette弃用警告；共享投递重构后22项核心路由/创建/真实AG-UI回归通过，套件有重合不相加。覆盖空目录创建、原话转交、无周期/Objective、并发重试恢复、不能同请求改名、失败保留Bot、原会话身份历史与跨用户隔离。Ruff和diff check通过。

只替换空闲Worker，不重启API/Web：API/Web仍20261103-chat-cadence-9354fdff，Worker为20261103-bot-create-fac266e9，image sha256:7b2c9a42a3330b011d4809c9e14bdbcd9b7a0c8eccd22518a17fffd576151ae4、93层。实际Worker安装包19个累计Python文件hash匹配，3服务healthy。compose SHA256=d43f359b04835d4f5414a771a38dc4d14d15ae1fcae50c69b9ff8c3baf555974。备份与日志/data/kai-bot-create-fac266e9（0700，compose备份0600）；schema0065无迁移，原卷、绑定、本人VM和浏览器保留。不动174，不恢复ZCode监督。API/Web容器启动时间仍08:14/08:15 UTC，只有Worker更新为08:54 UTC。

## 真实页面结果

原账号主助理实际发送创建“工时填写”、交原始需求到本人禅道执行#3231的请求，限定本轮不提交工时、不设周期。首次DeepSeek run_b5f1b1b5ec3a412492067f616c65d15d等待约5分钟未给工具决定，实际目录已含新创建工具，但没有创建/派发；停止这次本人发起的验收请求，未把它算成功。仅本次验收改选已配置MiniMax M3.1 Flash，完成后将主会话选择恢复DeepSeek。

重试主run_c6d65edfeed04b9dab34d309a17e4222实际direct/management → assistant_create_specialist，创建specialist_4d73fecfc5eb4b65bfd6503c211372d0，名称“工时填写”，ready、创建时间2026-11-03T09:01:05.318589Z。创建记录与真实投递回执分别存在：handover_49a25ebb498f48b6be27cd8372177754，targetThread=assistant_specialist_4d73fecfc5eb4b65bfd6503c211372d0，childRun=run_fe39a890e93e4598abf9008f70788309。子Run.input.prompt与父原始需求逐字相等、assistant_dispatch=true。职责数量始终15，没有因创建Bot多建周期或Objective。

Chrome侧栏出现“工时填写”，主会话显示“已发送消息给·工时填写”与创建回执；点击来源名进入它自己的会话，显示“消息来自·主助理”，没有绿色重复任务说明。子Run真实执行Bash环境/网络检查及本Bot记忆查询，配置阶段仍在运行；不将联系人ready等同于已登录、业务规则已确认、工时已提交或定时已启用。已向子会话补充：仅核对现有登录态/已授权连接，不扫描磁盘找凭据、不读未指定路径密码文件，缺项在本会话询问，仍不提交/设周期。

本轮创建及初始投递已实页验证；登录/工时规则/实际填报与后续生效由该Bot继续处理，不冒称这些业务验收完成。API/Web未重启，未修改KAI默认模型或平台模型配置，ZCode监督仍暂停。

后续只读与实页复核：首次DeepSeek父Run已cancelled；MiniMax父Run和子Run均succeeded。子Run的配置范围补充先后产生run.steer.requested/sending/accepted，没有另起一条子Run。主实页已自动出现“消息来自·工时填写”和初步配置核查摘要；该流程的创建→原话投递→子Bot实际处理→结果回主已跑通。子Bot要求补齐认证方式及工时规则，没有实际提交工时或设置周期。

审计边界：子Bot在收到范围补充前曾搜索浏览器目录和凭据相关文件名元数据；不把其最终“未扫描磁盘凭据”自述当作完整审计事实。已补充禁止继续扫描/读取未指定路径并获得accepted。配置表单仍出现“直接在对话提供账号密码”的生成选项，安全输入口径需后续统一到已授权Vault或本人登录，不宣称Vault运行时自动填充已完成。创建与转交修复不能冒充登录和填报能力完整验收。

飞书已追加11.12 revision97，后续完成状态在同节补记。监督继续暂停。

飞书完成补记revision98已局部回读正文确认。创建与初始配置回传完成，实际业务激活的剩余项留在新Bot中。
