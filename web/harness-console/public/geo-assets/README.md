# KAI 内置地图依赖

通过 79 从官方 npm 注册表 CDN 获取，固定版本，随前端部署，不依赖用户访问脚本 CDN。

- MapLibre GL JS 4.7.1: https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js
- MapLibre CSS 4.7.1: https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css
- Leaflet bridge 0.0.22: https://unpkg.com/@maplibre/maplibre-gl-leaflet@0.0.22/leaflet-maplibre-gl.js
- 对应许可证为同目录 `maplibre-LICENSE.txt` 与 `leaflet-maplibre-LICENSE.txt`。
- OpenFreeMap 使用说明: https://openfreemap.org/quick_start/

SHA256（本次下载文件）：

```json
{
  "maplibre-gl.js": "be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1",
  "maplibre-LICENSE.txt": "ee5fc05a0677eaf69601d2c7db0d9ecd6cc27c3abc1d0733bc9ed34707cf8ef2",
  "leaflet-maplibre-gl.js": "59ecd8d2b32779d24f1bd26b87c8890f27e4a6e7858d993bd17e4d878e6d2677",
  "maplibre-gl.css": "576b085fdd9487a65a19215328c1e086c07ce5bf6da09b666b3806d3d008dae9",
  "leaflet-maplibre-LICENSE.txt": "eaa721ba158cbeff47ad53b1035dfc26ff744df66662c93ff715c9885197ebf3"
}
```

## 沙盒兼容补丁（2026-10-15）

MapLibre 4.7.1 的 Actor 用 `location.origin` 标记和校验 Worker 消息。
在 `sandbox="allow-scripts"` 中，文档 URL 的 origin 与 blob Worker 的 `null` origin 不同，
导致消息被静默丢弃：样式成功但不请求瓦片。将 bundle 内 Actor 的 6 处
`location.origin` 改为 `self.origin`，双方使用实际执行来源；来源比较与 map ID 检查保持有效。
不需要添加 `allow-same-origin`，也没有放宽 CSP。

复现和验证：`tests/maplibre-sandbox.spec.ts` 执行 bundle 中实际 Actor，
覆盖 opaque origin 的发送/响应、异源和不同 map ID 的拒绝；79 隔离 iframe 实测可显示瓦片。
上游 6.14.0 的 `src/util/actor.ts` 也针对 `null` origin 加入了特殊处理；
这里保留已验证的 4.7.1 和 Leaflet bridge 组合，采用更窄的执行来源修复。

以上 SHA256 是原始下载值，部署文件的 SHA256 为：
`487268283628af8f99eb122b7a809a7468f1e46554c24dd0ba67847d9db5be0e`

补丁可重现：对 SHA256 为 `be9633c4d870e26fb37f1cfe5c5a77181667114003ea16207ac7850d8da8add1` 的原始 JS，确认 `location.origin` 恰好出现 6 次后，逐字替换为 `self.origin`；不修改其他代码。
