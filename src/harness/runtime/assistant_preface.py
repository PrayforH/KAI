"""One-shot nudge that makes the assistant speak before its first tool.

The assistant chat needs one public sentence before the first tool call, otherwise
the user only sees tool silence until the model answers. The model already owns the
wording, so this module never supplies text: it withholds the first tool once and
asks the model to say something first. The nudged tool call is not a real step, so
its result is kept out of the public projection.
"""

from dataclasses import dataclass, field

PREFACE_NUDGE_REASON = (
    "先向用户发一句简短的公开说明，说清你接下来要做什么，不要提问；然后再调用这个工具。"
)


@dataclass
class AssistantPreface:
    """Track the public progress of one Run on the assistant surface."""

    enabled: bool
    public_text_seen: bool = False
    nudged: bool = False
    nudged_tool_ids: set[str] = field(default_factory=set)

    def should_nudge(self) -> bool:
        """Nudge at most once, and only before any public text was produced."""

        return self.enabled and not self.public_text_seen and not self.nudged

    def note_public_text(self) -> None:
        self.public_text_seen = True

    def note_nudge(self, tool_use_id: str) -> None:
        self.nudged = True
        self.nudged_tool_ids.add(tool_use_id)

    def is_nudged_tool(self, tool_use_id: str) -> bool:
        return tool_use_id in self.nudged_tool_ids
