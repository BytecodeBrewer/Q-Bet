"""Prometheus-compatible, low-cardinality projections over durable Monitoring records."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping

from qbet.monitoring.models import MonitoringRecord

_DURATION_BUCKETS_MS = (10, 50, 100, 250, 500, 1_000, 5_000)


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**labels: str) -> str:
    rendered = ",".join(
        f'{name}="{_escape_label(value)}"' for name, value in sorted(labels.items())
    )
    return f"{{{rendered}}}" if rendered else ""


def render_prometheus_metrics(
    records: Iterable[MonitoringRecord],
    queue_counts: Mapping[tuple[str, str], int],
) -> str:
    """Render a bounded, non-authoritative operational telemetry snapshot.

    The supplied records represent a rolling observation window, so event and duration
    counts are gauges rather than monotonic process counters. Correlation IDs,
    user/work/opportunity identifiers, references, raw payloads, and provider/account
    identifiers are deliberately excluded from labels and metric values.
    """

    event_counts: Counter[tuple[str, str, str, str, str]] = Counter()
    lifecycle_counts: Counter[tuple[str, str]] = Counter()
    issue_counts: Counter[tuple[str, str]] = Counter()
    durations: dict[tuple[str, str, str], list[int]] = defaultdict(list)

    for record in records:
        engine = str(record.engine)
        mode = str(record.mode) if record.mode is not None else "none"
        stage = str(record.stage)
        status = str(record.status)
        level = record.level.value
        event_counts[(engine, mode, stage, status, level)] += 1

        if record.event_type == "lifecycle" and mode in {"simulation", "execution"}:
            lifecycle_counts[(mode, status)] += 1
        if level in {"warning", "error"}:
            issue_counts[(level, stage)] += 1
        if record.duration_ms is not None:
            durations[(engine, mode, stage)].append(record.duration_ms)

    lines = [
        "# HELP qbet_monitoring_events_window Persisted Monitoring events in the scrape window.",
        "# TYPE qbet_monitoring_events_window gauge",
    ]
    for (engine, mode, stage, status, level), count in sorted(event_counts.items()):
        lines.append(
            "qbet_monitoring_events_window"
            + _labels(engine=engine, mode=mode, stage=stage, status=status, level=level)
            + f" {count}"
        )

    lines.extend(
        [
            "# HELP qbet_lifecycle_outcomes_window Lifecycle outcomes in the scrape window.",
            "# TYPE qbet_lifecycle_outcomes_window gauge",
        ]
    )
    for (mode, status), count in sorted(lifecycle_counts.items()):
        lines.append(
            "qbet_lifecycle_outcomes_window" + _labels(mode=mode, status=status) + f" {count}"
        )

    lines.extend(
        [
            "# HELP qbet_operational_issues_window Warnings/errors in the scrape window.",
            "# TYPE qbet_operational_issues_window gauge",
        ]
    )
    for (level, stage), count in sorted(issue_counts.items()):
        lines.append(
            "qbet_operational_issues_window" + _labels(level=level, stage=stage) + f" {count}"
        )

    lines.extend(
        [
            "# HELP qbet_stage_duration_observations_ms Bounded stage-duration distribution.",
            "# TYPE qbet_stage_duration_observations_ms gauge",
        ]
    )
    for (engine, mode, stage), values in sorted(durations.items()):
        base_labels = {"engine": engine, "mode": mode, "stage": stage}
        for bucket in _DURATION_BUCKETS_MS:
            bucket_count = sum(value <= bucket for value in values)
            lines.append(
                "qbet_stage_duration_observations_ms"
                + _labels(**base_labels, le=str(bucket))
                + f" {bucket_count}"
            )
        lines.append(
            "qbet_stage_duration_observations_ms"
            + _labels(**base_labels, le="+Inf")
            + f" {len(values)}"
        )

    lines.extend(
        [
            "# HELP qbet_queue_items Current durable queue items by mode and state.",
            "# TYPE qbet_queue_items gauge",
        ]
    )
    for (mode, state), count in sorted(queue_counts.items()):
        lines.append("qbet_queue_items" + _labels(mode=mode, state=state) + f" {count}")

    return "\n".join(lines) + "\n"
