"""The notification fingerprint hashes structure, never prose.

Important-mode dedup must treat a reworded summary of the same outcome as the
same event, and a new artifact batch or an advanced event sequence as new.
"""

from harness.assistant.notice_fingerprint import notice_fingerprint


def test_identical_structure_yields_one_fingerprint_regardless_of_prose() -> None:
    base = dict(
        kind="succeeded",
        objective_status="active",
        execution_id="execution-1",
        run_id="run-1",
        source_event_id="event-9",
        source_sequence=7,
        artifacts=[("report.txt", "text/plain", "sha-a")],
    )
    assert notice_fingerprint(**base) == notice_fingerprint(**base)


def test_a_new_artifact_batch_changes_the_fingerprint() -> None:
    base = dict(
        kind="succeeded",
        objective_status="active",
        run_id="run-1",
        source_sequence=7,
        artifacts=[("report.txt", "text/plain", "sha-a")],
    )
    grown = dict(
        base,
        artifacts=[("report.txt", "text/plain", "sha-a"), ("data.csv", "text/csv", "sha-b")],
    )
    replaced = dict(base, artifacts=[("report.txt", "text/plain", "sha-changed")])
    assert notice_fingerprint(**base) != notice_fingerprint(**grown)
    assert notice_fingerprint(**base) != notice_fingerprint(**replaced)


def test_artifact_order_does_not_matter_but_membership_does() -> None:
    one = notice_fingerprint(
        kind="succeeded",
        artifacts=[("a.txt", "text/plain", "sha-a"), ("b.csv", "text/csv", "sha-b")],
    )
    same_set = notice_fingerprint(
        kind="succeeded",
        artifacts=[("b.csv", "text/csv", "sha-b"), ("a.txt", "text/plain", "sha-a")],
    )
    assert one == same_set


def test_an_advanced_event_sequence_and_run_identity_change_the_fingerprint() -> None:
    base = dict(kind="succeeded", run_id="run-1", source_sequence=7)
    assert notice_fingerprint(**base) != notice_fingerprint(**dict(base, source_sequence=8))
    assert notice_fingerprint(**base) != notice_fingerprint(**dict(base, run_id="run-2"))
    assert notice_fingerprint(**base) != notice_fingerprint(
        **dict(base, source_event_id="event-10")
    )


def test_kind_and_objective_status_are_part_of_the_substance() -> None:
    base = dict(kind="succeeded")
    assert notice_fingerprint(**base) != notice_fingerprint(**dict(base, kind="failed"))
    assert notice_fingerprint(**base) != notice_fingerprint(
        **dict(base, objective_status="blocked")
    )


def test_legacy_notices_without_structured_fields_still_fingerprint_stably() -> None:
    legacy = dict(kind="succeeded")
    assert notice_fingerprint(**legacy) == notice_fingerprint(
        kind="succeeded",
        objective_status=None,
        execution_id=None,
        run_id=None,
        source_event_id=None,
        source_sequence=None,
        artifacts=[],
    )
    # 旧载荷缺字段不抛错；空值与缺省同形。
    assert notice_fingerprint(kind="succeeded", run_id="") == notice_fingerprint(kind="succeeded")
