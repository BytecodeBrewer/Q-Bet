"""Stable, low-cardinality Prometheus/OpenMetrics rendering."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ObservabilitySnapshot:
    """Aggregate-only operational values from the durable control plane."""

    available: bool
    monitoring_events: Mapping[tuple[str, str, str], int]
    monitoring_durations: Mapping[tuple[str, str], tuple[int, int]]
    queue_items: Mapping[tuple[str, str], int]
    execution_records: Mapping[tuple[str, str], int]
    latest_monitoring_timestamp_seconds: int | None = None
    monitoring_failures: Mapping[tuple[str, str, str], int] = field(default_factory=dict)

    @classmethod
    def unavailable(cls) -> "ObservabilitySnapshot":
        return cls(False, {}, {}, {}, {})


def metric_dimensions(engine: str, mode: str | None) -> tuple[str, str]:
    """Reduce durable identifiers to the finite metric dimensions."""

    return (
        engine if engine in {"bonus", "sports_capital"} else "other",
        mode if mode in {"simulation", "execution"} else "other",
    )


def prometheus_document(snapshot: ObservabilitySnapshot) -> bytes:
    """Render a deterministic, aggregate-only Prometheus text document."""

    lines = [
        "# HELP qbet_observability_source_available Whether durable observability data is readable.",
        "# TYPE qbet_observability_source_available gauge",
        f"qbet_observability_source_available {int(snapshot.available)}",
        "# HELP qbet_monitoring_events_total Durable monitoring events by stable dimensions.",
        "# TYPE qbet_monitoring_events_total counter",
    ]
    for (engine, mode, level), count in sorted(snapshot.monitoring_events.items()):
        lines.append(
            "qbet_monitoring_events_total"
            f'{{engine="{engine}",mode="{mode}",level="{level}"}} {count}'
        )
    lines.extend(
        (
            "# HELP qbet_monitoring_duration_milliseconds Durable observed monitoring duration.",
            "# TYPE qbet_monitoring_duration_milliseconds summary",
        )
    )
    for (engine, mode), (count, total) in sorted(snapshot.monitoring_durations.items()):
        labels = f'engine="{engine}",mode="{mode}"'
        lines.append(f"qbet_monitoring_duration_milliseconds_count{{{labels}}} {count}")
        lines.append(f"qbet_monitoring_duration_milliseconds_sum{{{labels}}} {total}")
    lines.extend(
        (
            "# HELP qbet_monitoring_latest_event_timestamp_seconds Unix timestamp of the newest durable monitoring event.",
            "# TYPE qbet_monitoring_latest_event_timestamp_seconds gauge",
            "qbet_monitoring_latest_event_timestamp_seconds "
            f"{snapshot.latest_monitoring_timestamp_seconds or 0}",
            "# HELP qbet_monitoring_failures_total Durable failed or retry monitoring outcomes.",
            "# TYPE qbet_monitoring_failures_total counter",
        )
    )
    for (engine, mode, outcome), count in sorted(snapshot.monitoring_failures.items()):
        lines.append(
            "qbet_monitoring_failures_total"
            f'{{engine="{engine}",mode="{mode}",outcome="{outcome}"}} {count}'
        )
    lines.extend(
        (
            "# HELP qbet_queue_items Durable workflow queue items by mode and state.",
            "# TYPE qbet_queue_items gauge",
        )
    )
    for (mode, state), count in sorted(snapshot.queue_items.items()):
        lines.append(f'qbet_queue_items{{mode="{mode}",state="{state}"}} {count}')
    lines.extend(
        (
            "# HELP qbet_execution_records Durable execution records by mode and state.",
            "# TYPE qbet_execution_records gauge",
        )
    )
    for (mode, state), count in sorted(snapshot.execution_records.items()):
        lines.append(f'qbet_execution_records{{mode="{mode}",state="{state}"}} {count}')
    return ("\n".join(lines) + "\n").encode()


def aggregate_monitoring(
    values: tuple[tuple[str, str | None, str, int | None], ...],
) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str], tuple[int, int]]]:
    """Aggregate only fixed value sets; IDs, reasons and payloads never become labels."""

    events: defaultdict[tuple[str, str, str], int] = defaultdict(int)
    durations: defaultdict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for engine, mode, level, duration_ms in values:
        metric_engine, metric_mode = metric_dimensions(engine, mode)
        metric_level = level if level in {"info", "warning", "error"} else "other"
        events[(metric_engine, metric_mode, metric_level)] += 1
        if duration_ms is not None:
            durations[(metric_engine, metric_mode)][0] += 1
            durations[(metric_engine, metric_mode)][1] += duration_ms
    return dict(events), {key: (value[0], value[1]) for key, value in durations.items()}
