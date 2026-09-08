"""Shared response projection for production chat and Studio preview."""
from harness.core.events import RunEvent
from harness.runtime.message_mapper import safe_model_text

_RESPONSE_BOUNDARY_PREFIXES = ("approval.", "subagent.", "tool.")

def final_response_text(events: list[RunEvent]) -> str:
    """Return only the answer emitted after the last auditable action.

    Providers stream progress commentary and final prose through the same
    message.delta channel. Activity renders the former in the execution
    timeline; history must not concatenate it into the final answer again.
    """

    last_action_index = -1
    for index, event in enumerate(events):
        if event.type.startswith(_RESPONSE_BOUNDARY_PREFIXES):
            last_action_index = index
    return "".join(
        safe_model_text(str(event.payload.get("text", "")))
        for index, event in enumerate(events)
        if index > last_action_index and event.type == "message.delta"
    )


def active_response_text(events: list[RunEvent]) -> str:
    """Restore only the current answer candidate for an active run.

    Progress commentary that was followed by a tool belongs in Activity, not
    in the response slot.  When a user returns from Studio during that tool
    pause there may therefore be no response text yet.  Once the provider
    starts emitting after the latest auditable action, restore only that newest
    message so the response grows in place without replaying earlier progress.
    """

    last_action_index = max(
        (
            index
            for index, event in enumerate(events)
            if event.type.startswith(_RESPONSE_BOUNDARY_PREFIXES)
        ),
        default=-1,
    )

    latest_message_id = next(
        (
            str(event.payload.get("message_id", "")).strip()
            for index, event in reversed(list(enumerate(events)))
            if index > last_action_index
            and event.type == "message.delta"
            and str(event.payload.get("message_id", "")).strip()
        ),
        "",
    )
    if latest_message_id:
        return "".join(
            safe_model_text(str(event.payload.get("text", "")))
            for index, event in enumerate(events)
            if index > last_action_index
            and event.type == "message.delta"
            and str(event.payload.get("message_id", "")).strip()
            == latest_message_id
        )

    latest_start_index = max(
        (
            index
            for index, event in enumerate(events)
            if index > last_action_index and event.type == "message.start"
        ),
        default=last_action_index + 1,
    )
    return "".join(
        safe_model_text(str(event.payload.get("text", "")))
        for index, event in enumerate(events)
        if index >= latest_start_index
        and index > last_action_index
        and event.type == "message.delta"
    )


