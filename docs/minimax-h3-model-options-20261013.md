# MiniMax 视频模型选择与补充信息跳过

为官方视频新增 `minimax_videos_v2` 协议，对接 `/v2/video_generation` 和
`/v2/query/video_generation/{task_id}`。Hailuo 2.3 保持 V1 接口和原有任务。

| 模型 | 界面时长档位 | 分辨率 |
| --- | --- | --- |
| MiniMax-Hailuo-2.3 | 6、10 秒 | 768P；1080P 仅 6 秒 |
| MiniMax-H3 | 5、10、15 秒 | 768P、2K |
| MiniMax-H3-Max | 5、10、15 秒 | 480P、768P |

沿用模型选择器、28px 视频参数行和浮动弹层，不扩大输入框。切换模型重置为兼容参数。
官方 H3 支持文本及首尾两张图片，输出链接直接用于播放；明确下载时由认证接口读取，
不向 CDN 转发模型密钥。任务沿用持久化、身份校验和刷新恢复。停止等待不会删除远端任务。
生成只提交一次，不按套餐次数预先拦截，不自动重试付费请求。

补充信息卡片新增“跳过”：保留已填写答案，将未回答问题标为跳过，并明确要求根据现有信息继续、
必要信息不足时说明限制。提交中或会话锁定时不可重复操作，失败保留答案。

使用模拟响应验证生成、查询、下载和参数错误；真实接口仅做读取任务列表的连接检查。
H3 / H3-Max 官方要求按量付费 API，任务列表可读取不代表余额或所有生成权限已确认。

官方依据：
- https://platform.minimax.io/docs/api-reference/video-generation-v2-create
- https://platform.minimax.io/docs/api-reference/video-generation-v2-query
- https://platform.minimax.io/docs/api-reference/video-generation-t2v
