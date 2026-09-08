# KAI 标志细化

用户确认方向：标志位于左上角；小尺寸蓝色应可辨认，但不接受整个大 A 填成蓝色。

采用原轮廓与局部蓝色斜边的透明 PNG：`web/harness-console/public/brand/kai-mark-v2.png`。共享品牌组件、favicon 和加载标志使用同一素材；深色主题将深色主体转浅，保留色相。旧素材不删除。favicon 内嵌 PNG，随 Next 构建更新资源标识；组件使用新文件名避开旧缓存。

本次 imagegen 编辑原图：`web/harness-console/public/brand/kai-mark.png`。

最终提示词：

> Refine this exact KAI angular monogram logo. Preserve the original silhouette, all angles and openings exactly. Keep almost all of the mark charcoal black. Only thicken the existing single cyan diagonal seam between the two mountains into a modest clean cyan-blue accent ribbon (#009BDF), taking just 8-10 percent of the total colored mark area, never fill the large A blue. The cyan ribbon should be around 1/4 the width of the adjacent black diagonal stroke: visible yet restrained. No other blue areas. Flat solid colors, no glow, no gradients, no lettering. Genuine transparent PNG. Center entire intact monogram inside a square canvas with 5 percent horizontal margin, avoid clipping. This is a restrained corporate logo with a small blue accent.

生成结果是 1254 × 1254 RGBA，已检查透明通道。提示词中的面积比例是设计目标，不是输出像素比例的测量声明。
