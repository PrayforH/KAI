"use client";
import { setDetailedProcess, useDetailedProcess } from "../lib/process-display-preference";
export function ProcessDisplaySettings() {
  const detailed = useDetailedProcess();
  return <label className="settings-preference-toggle">
    <input type="checkbox" checked={detailed} onChange={event => setDetailedProcess(event.target.checked)} />
    <span><strong>详细执行过程</strong><small>逐项展示工具动作，关闭时合并同类动作。思考默认显示单行预览，点击可展开全文。</small></span>
  </label>;
}
