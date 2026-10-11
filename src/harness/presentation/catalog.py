"""What the platform is willing to render, and the contract that advertises it.

The catalog is the single place that says which presentation kinds exist, which
renderer version serves them and what limits apply. ``UI_BLOCK_CONTRACT`` is
generated from the same entries, so the model-facing description cannot drift
from the validator.

Errors here never reach the user as a widget: an unknown kind or a rejected block
leaves the answer exactly as the model wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness.presentation.schema import (
    MAX_ACTIONS,
    MAX_CHART_SERIES,
    MAX_ITEMS,
    MAX_ROWS,
    MAX_STRING,
)

# How many presentations a single assistant turn may show. Text stays the
# default; a widget has to earn its place, and the server enforces the cap
# rather than trusting the model to be restrained.
MAX_PRESENTATIONS_PER_TURN = 2


@dataclass(frozen=True)
class Renderer:
    kind: str
    version: str
    summary: str
    data_shape: str
    config_shape: str = ""
    field_types: str = ""


_RENDERERS: tuple[Renderer, ...] = (
    Renderer(
        kind="table",
        version="table/1",
        summary="逐行可比的结构化记录，用户需要排序或核对具体数值时",
        data_shape='{"columns":[{"key":"a","label":"名称"}],"rows":[{"a":"值"}]}',
        config_shape='{"caption":"可选说明"}',
    ),
    Renderer(
        kind="comparison",
        version="comparison/1",
        summary="少量对象在同一组维度上的横向对比",
        data_shape=(
            '{"dimensions":[{"key":"price","label":"价格"}],'
            '"items":[{"label":"方案甲","values":{"price":"120"}},'
            '{"label":"方案乙","values":{"price":"98"}}]}'
        ),
    ),
    Renderer(
        kind="chart",
        version="chart/1",
        summary="带单位或时间维度的数值趋势与分布",
        data_shape='{"rows":[{"q":"第一季度","count":120}]}',
        config_shape=(
            '{"chart_type":"bar|line|area|pie","x_field":"q","y_fields":["count"],'
            '"y_labels":["处理量"],"unit":"件","x_label":"季度","y_label":"数量"}'
        ),
    ),
    Renderer(
        kind="diagram",
        version="diagram/1",
        summary="流程、依赖或层级关系；用 Mermaid 描述节点与边",
        data_shape='{"mermaid":"flowchart LR\\n  A[受理] --> B[审批]"}',
    ),
    Renderer(
        kind="timeline",
        version="timeline/1",
        summary="按时间推进的关键节点",
        data_shape='{"events":[{"at":"2026-03","title":"立项","detail":"可选"}]}',
    ),
    Renderer(
        kind="metric",
        version="metric/1",
        summary="少量关键标量指标（不超过 8 项）",
        data_shape='{"items":[{"label":"转化率","value":"18.4","unit":"%"}]}',
    ),
    Renderer(
        kind="form",
        version="form/1",
        summary="需要用户补齐的字段，一次收集后提交",
        data_shape=(
            '{"fields":[{"key":"name","label":"项目名称","type":"text","required":true},'
            '{"key":"level","label":"优先级","type":"single","options":["高","中","低"]}]}'
        ),
        config_shape='{"submit_label":"提交"}',
        field_types="字段 type 取 text/number/date/single/multi；"
        "single 与 multi 必须给 2–8 个 options。",
    ),
)

def _renderers_text() -> str:
    lines: list[str] = []
    for renderer in _RENDERERS:
        detail = f"    data: {renderer.data_shape}"
        if renderer.config_shape:
            detail += f"\n    config: {renderer.config_shape}"
        if renderer.field_types:
            detail += f"\n    {renderer.field_types}"
        lines.append(f"- {renderer.kind} — {renderer.summary}\n{detail}")
    return "\n".join(lines)


UI_BLOCK_CONTRACT = f"""## Interactive answer blocks

Text is the default. Answer directly in prose and Markdown unless a structured
answer genuinely helps the user read, compare or verify the result. Do not add a
block to make a reply look richer: the platform shows at most
{MAX_PRESENTATIONS_PER_TURN} per turn, and a wrong widget costs the user more than
plain text.

When a block does help, end your turn with one fenced ```harness-ui block holding a
single JSON object and nothing after the closing fence:

{{"version":1,"kind":"table","title":"可选标题",
 "fallback_text":"一句话说明这个块展示了什么，不含 JSON",
 "data":{{...}},"config":{{...}}}}

Available kinds:
{_renderers_text}

Rules:
- `fallback_text` is required and must stand alone: it is what a client without
  this renderer shows instead. Never leave it as the only copy of a conclusion.
- Use only fields that appear in the shapes above. Unknown keys, unknown kinds or
  an out-of-range size reject the whole block and the user sees your raw JSON —
  check the shape before you write it.
- A column or dimension may name itself `key` or `id`, or be written as a bare
  string when its name is all it has. A `comparison` item may name itself
  `label`, `key` or `name`, and its `values` may be an object keyed by dimension
  or a list in dimension order. Nothing else is inferred.
- `data.rows`/`items`/`events` must be complete; the platform never fetches data
  for a block and never edits business state from one.
- Keep every cell a JSON string, number, boolean or null. Never nest objects in a
  cell; flatten or drop the field instead.
- Rows are capped at {MAX_ROWS}, cells at {MAX_STRING} characters, chart series at
  {MAX_CHART_SERIES}, comparison items at {MAX_ITEMS}. Aggregate before you emit.
- Say the conclusion in prose as well. The block illustrates the answer; it is not
  a replacement for it.

### Letting the user act on a block

A block may carry up to {MAX_ACTIONS} `actions`. The platform assigns each one an
`action_id`; you only declare what the button says and what it should ask for:

  "actions":[{{"kind":"conversation","label":"解释这一行","prompt":"请展开说明这一行的口径"}},
             {{"kind":"read","label":"拉取最新数据","prompt":"请重新查询并给出最新数据"}},
             {{"kind":"write","label":"提交给审批","prompt":"请按这些字段发起提交"}}]

- `conversation` continues the explanation; `read` asks for fresh data; `write`
  starts work the platform will run through its normal policy and approval. All
  three become an ordinary user turn, so you will receive them as the next
  message. A `local` interaction (sorting a table, toggling a series) is the
  component's own affordance and needs no action.
- Write only what the user would plausibly want to do next, and never more than
  one primary action (`"style":"primary"`). Actions are a shortcut for the user,
  not a menu: an action that does nothing useful is worse than none.
- A `form` block's submit button is its action, so a form must declare one `read`
  or `write` action; one with nowhere to submit is rejected. Give it a
  `submit_label` that names the real operation, and never claim it already
  happened."""


class UICapabilityCatalog:
    """The computed allow-set for one deployment."""

    def __init__(self, renderers: tuple[Renderer, ...], *, generated_html: bool = False) -> None:
        self._renderers = {renderer.kind: renderer for renderer in renderers}
        self.generated_html = generated_html

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._renderers))

    def renderer(self, kind: str) -> Renderer | None:
        return self._renderers.get(kind)

    def describe(self) -> dict[str, object]:
        return {
            "schema_version": "harness.ui/v1",
            "max_presentations_per_turn": MAX_PRESENTATIONS_PER_TURN,
            "generated_html": self.generated_html,
            "renderers": [
                {
                    "kind": renderer.kind,
                    "renderer_version": renderer.version,
                    "summary": renderer.summary,
                    "data_shape": renderer.data_shape,
                    "config_shape": renderer.config_shape,
                }
                for renderer in self._renderers.values()
            ],
        }


def default_catalog() -> UICapabilityCatalog:
    return UICapabilityCatalog(_RENDERERS)
