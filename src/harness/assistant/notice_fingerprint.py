"""Structured substantive-change fingerprint for assistant notifications.

Important-mode dedup must not hinge on prose: a reworded summary (timestamps,
synonyms) of the same outcome is still the same event, while a new artifact, an
advanced event sequence or a new run is genuinely new. The fingerprint hashes
the structured identity and outcome fields only — free-text content is
deliberately excluded so wording cannot flip the decision either way.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

# One published outcome: (name, media_type, content digest). Absent fields stay
# empty rather than inventing values.
ArtifactSignature = tuple[str, str, str]


def notice_fingerprint(
    *,
    kind: str,
    objective_status: str | None = None,
    execution_id: str | None = None,
    run_id: str | None = None,
    source_event_id: str | None = None,
    source_sequence: int | None = None,
    artifacts: Sequence[ArtifactSignature] = (),
) -> str:
    """Hash the structured substance of one notification.

    ``artifacts`` carries (name, media_type, content digest) triples when the
    channel can observe them; a notice without artifact information simply
    contributes none. Fields join NUL-separated with their names so the same
    values in different slots can never collide into one fingerprint.
    """
    parts = [
        "kind",
        kind,
        "objective",
        objective_status or "",
        "execution",
        execution_id or "",
        "run",
        run_id or "",
        "event",
        source_event_id or "",
        "sequence",
        str(source_sequence) if source_sequence is not None else "",
    ]
    # Sorted so the artifact set is order-independent: publishing the same files
    # in a different order is the same outcome, a different set is not.
    for name, media_type, content_digest in sorted(artifacts):
        parts.extend(("artifact", name, media_type, content_digest))
    return hashlib.sha256("\0".join(parts).encode()).hexdigest()
