from __future__ import annotations

import math
import threading
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    help: str
    kind: str
    allowed_labels: tuple[str, ...] = ()


DEFINITIONS = (
    MetricDefinition(
        "harness_api_request_duration_seconds",
        "Harness API request duration by bounded operation.",
        "summary",
        ("operation",),
    ),
    MetricDefinition(
        "harness_event_visibility_delay_seconds",
        "Delay from durable event timestamp until a consumer reads it.",
        "summary",
    ),
    MetricDefinition(
        "harness_workflow_convergence_seconds",
        "End-to-end convergence time for bounded control workflows.",
        "summary",
        ("workflow",),
    ),
    MetricDefinition(
        "harness_run_stage_duration_seconds",
        "Run latency for bounded execution stages.",
        "summary",
        ("stage",),
    ),
    MetricDefinition(
        "harness_worker_queue_failures_total",
        "Worker queue operation failures by bounded operation.",
        "counter",
        ("operation",),
    ),
    MetricDefinition(
        "harness_artifact_download_total",
        "Artifact download attempts by outcome.",
        "counter",
        ("outcome",),
    ),
    MetricDefinition(
        "harness_trace_terminal_total",
        "Terminal runs by trace completeness.",
        "counter",
        ("completeness",),
    ),
    MetricDefinition(
        "harness_reaper_actions_total",
        "Reaper actions by bounded reaper and outcome.",
        "counter",
        ("reaper", "outcome"),
    ),
    MetricDefinition(
        "harness_stuck_runs",
        "Runs currently older than their state threshold.",
        "gauge",
        ("status",),
    ),
    MetricDefinition(
        "harness_queue_tasks",
        "Run tasks by queue state.",
        "gauge",
        ("state",),
    ),
    MetricDefinition(
        "harness_capacity_resource",
        "Current platform capacity fact.",
        "gauge",
        ("resource",),
    ),
)

LABEL_VALUE_ALLOWLISTS: dict[tuple[str, str], frozenset[str]] = {
    ("harness_api_request_duration_seconds", "operation"): frozenset(
        {
            "run.create",
            "run.cancel",
            "approval.decide",
            "artifact.download",
            "other",
        }
    ),
    ("harness_artifact_download_total", "outcome"): frozenset(
        {"success", "failure"}
    ),
    ("harness_workflow_convergence_seconds", "workflow"): frozenset(
        {"run.cancel", "approval.decide"}
    ),
    ("harness_run_stage_duration_seconds", "stage"): frozenset(
        {
            "queue_wait",
            "environment_prepare",
            "runtime_first_event",
            "provider_first_event",
            "runtime_first_text",
        }
    ),
    ("harness_worker_queue_failures_total", "operation"): frozenset(
        {"dequeue", "acknowledge", "retry", "extend_lease"}
    ),
    ("harness_trace_terminal_total", "completeness"): frozenset(
        {"complete", "missing"}
    ),
    ("harness_reaper_actions_total", "reaper"): frozenset(
        {
            "stuck-run",
            "approval-expiry",
            "preview-expiry",
            "quota-reservation",
            "workspace-retention",
            "credential-lease",
            "sandbox-expiry",
            "memory-expiry",
        }
    ),
    ("harness_reaper_actions_total", "outcome"): frozenset(
        {"reaped", "skipped", "failed"}
    ),
    ("harness_stuck_runs", "status"): frozenset(
        {"queued", "provisioning", "running", "waiting_approval", "cancelling"}
    ),
    ("harness_queue_tasks", "state"): frozenset({"ready", "processing"}),
    ("harness_capacity_resource", "resource"): frozenset(
        {
            "active_previews",
            "pending_approvals",
            "artifact_bytes",
            "snapshot_bytes",
            "lifecycle_backlog",
            "credential_leases",
            "active_sandboxes",
            "database_pool_checked_out",
        }
    ),
}


def _labels_key(
    definition: MetricDefinition,
    labels: Mapping[str, str] | None,
) -> tuple[tuple[str, str], ...]:
    supplied = dict(labels or {})
    values: list[tuple[str, str]] = []
    for name in definition.allowed_labels:
        value = supplied.get(name, "unknown")
        allowlist = LABEL_VALUE_ALLOWLISTS.get((definition.name, name))
        values.append((name, value if allowlist is None or value in allowlist else "unknown"))
    return tuple(values)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class ReliabilityMetrics:
    """Small bounded registry shared by API, workers and the operations page."""

    def __init__(self, *, max_observations: int = 10_000) -> None:
        self._definitions = {item.name: item for item in DEFINITIONS}
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._observations: dict[
            tuple[str, tuple[tuple[str, str], ...]], deque[float]
        ] = {}
        self._max_observations = max_observations
        self._lock = threading.Lock()

    def increment(
        self,
        name: str,
        amount: float = 1,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        definition = self._definitions[name]
        key = (name, _labels_key(definition, labels))
        with self._lock:
            self._values[key] += amount

    def gauge(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        definition = self._definitions[name]
        key = (name, _labels_key(definition, labels))
        with self._lock:
            self._values[key] = value

    def observe(
        self,
        name: str,
        value: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> None:
        if value < 0 or not math.isfinite(value):
            return
        definition = self._definitions[name]
        key = (name, _labels_key(definition, labels))
        with self._lock:
            observations = self._observations.setdefault(
                key, deque(maxlen=self._max_observations)
            )
            observations.append(value)

    def count(
        self, name: str, *, labels: Mapping[str, str] | None = None
    ) -> float:
        definition = self._definitions[name]
        key = (name, _labels_key(definition, labels))
        with self._lock:
            return self._values.get(key, 0)

    def quantile(
        self,
        name: str,
        quantile: float,
        *,
        labels: Mapping[str, str] | None = None,
    ) -> tuple[float | None, int]:
        definition = self._definitions[name]
        key = (name, _labels_key(definition, labels))
        with self._lock:
            values = sorted(self._observations.get(key, ()))
        if not values:
            return None, 0
        index = min(len(values) - 1, max(0, math.ceil(quantile * len(values)) - 1))
        return values[index], len(values)

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            values = dict(self._values)
            observations = {key: tuple(value) for key, value in self._observations.items()}
        for definition in DEFINITIONS:
            lines.append(f"# HELP {definition.name} {definition.help}")
            lines.append(f"# TYPE {definition.name} {definition.kind}")
            if definition.kind == "summary":
                matches = sorted(
                    (key, samples)
                    for key, samples in observations.items()
                    if key[0] == definition.name
                )
                for (_, labels), samples in matches:
                    for quantile in (0.5, 0.95, 0.99):
                        value = sorted(samples)[
                            min(len(samples) - 1, math.ceil(quantile * len(samples)) - 1)
                        ]
                        lines.append(
                            self._sample(
                                definition.name,
                                (*labels, ("quantile", str(quantile))),
                                value,
                            )
                        )
                    lines.append(
                        self._sample(f"{definition.name}_sum", labels, sum(samples))
                    )
                    lines.append(
                        self._sample(f"{definition.name}_count", labels, len(samples))
                    )
            else:
                for (_, labels), value in sorted(
                    (key, value)
                    for key, value in values.items()
                    if key[0] == definition.name
                ):
                    lines.append(self._sample(definition.name, labels, value))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _sample(
        name: str, labels: tuple[tuple[str, str], ...], value: float
    ) -> str:
        suffix = (
            "{" + ",".join(f'{key}="{_escape(child)}"' for key, child in labels) + "}"
            if labels
            else ""
        )
        return f"{name}{suffix} {value:g}"
