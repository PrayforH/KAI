import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { CompactionContent } from "../src/components/context-compaction-history";
import { loadCompactionDetail, loadThreadCompactions, type CompactionDetail } from "../src/lib/context-client";

const detail: CompactionDetail = {
  run_id: "run", runtime: "deepagents", status: "available", reason: null, compaction_count: 1,
  before: { source_run_id: "before", message_count: 1, characters: 12949,
    messages: [{ role: "user", content: "项目预算48万元", truncated: false }], truncated: false },
  after: { source_run_id: "run", message_count: 3, characters: 1153, truncated: false,
    messages: [
      { role: "user", content: "wrapper<summary>项目预算48万元</summary>", truncated: false },
      { role: "user", content: "调整到52万元", truncated: false },
      { role: "assistant", content: "已记录", truncated: false },
    ] },
  summary: { role: "user", content: "wrapper<summary>项目预算48万元</summary>", truncated: false },
};

describe("compaction inspection", () => {
  it("distinguishes history checkpoints from complete requests and shows actual summary plus tail", () => {
    const html = renderToStaticMarkup(<CompactionContent detail={detail} />);
    expect(html).toContain("12,949");
    expect(html).toContain("1,153");
    expect(html).toContain("不含系统提示词和工具定义");
    expect(html).toContain("压缩后摘要");
    expect(html).toContain("压缩前历史参考");
    expect(html).toContain("调整到52万元");
    expect(html).not.toContain("wrapper");
  });
  it("does not invent text for runtimes that only report compaction metadata", () => {
    const html = renderToStaticMarkup(<CompactionContent detail={{ ...detail, status: "unavailable", summary: null, reason: "运行时未提供正文" }} />);
    expect(html).toContain("运行时未提供正文");
    expect(html).not.toContain("项目预算");
  });
  it("labels clipped content and multiple compactions", () => {
    const html = renderToStaticMarkup(<CompactionContent detail={{ ...detail, compaction_count: 2, before: { ...detail.before!, truncated: true } }} />);
    expect(html).toContain("最后一次摘要");
    expect(html).toContain("当前展示已截断");
  });
  it("fetches metadata separately from lazy body with safely encoded identifiers", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], has_older: false })));
    await loadThreadCompactions("thread/a", fetcher);
    expect(fetcher).toHaveBeenCalledWith("/api/agui/threads/thread%2Fa/context/compactions", { cache: "no-store" });
    fetcher.mockResolvedValue(new Response(JSON.stringify(detail)));
    expect(await loadCompactionDetail("thread/a", "run/b", fetcher)).toEqual(detail);
    expect(fetcher).toHaveBeenLastCalledWith("/api/agui/threads/thread%2Fa/context/compactions/run%2Fb", { cache: "no-store" });
  });
});
